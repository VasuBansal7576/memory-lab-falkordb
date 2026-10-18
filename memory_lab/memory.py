import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from falkordb import FalkorDB
from redis.exceptions import RedisError

from .domain import Document, PREDICATES
from .context import compact_evidence
from . import retrieval
from .settings import settings
from .questions import plan_question, question_shape, resolve_mentions, supported_shape, APPROVER_QUERY


class MemoryConflict(Exception):
    pass


class MemoryUnavailable(Exception):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _canonical(documents):
    result = []
    for document in sorted(documents, key=lambda d: d.id):
        data = document.model_dump()
        data['entities'].sort(key=lambda e: e['id'])
        data['facts'].sort(key=_json)
        result.append(data)
    return result


def _entities(documents):
    entities = {}
    for document in documents:
        for entity in document['entities']:
            previous = entities.get(entity['id'])
            if previous and (previous['kind'], previous['name']) != (entity['kind'], entity['name']):
                raise ValueError(f'Entity {entity["id"]} has conflicting identity across documents')
            aliases = set(entity['aliases']) | set(previous['aliases'] if previous else [])
            descriptions = {entity.get('description', ''), previous.get('description', '') if previous else ''} - {''}
            entities[entity['id']] = {**entity, 'aliases': sorted(aliases), 'description': '\n'.join(sorted(descriptions))}
    return sorted(entities.values(), key=lambda e: e['id'])


class Memory:
    def __init__(self, host='127.0.0.1', port=6387, namespace='graph_memory_lab'):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', namespace):
            raise ValueError('Namespace must contain only letters, digits, underscores, and hyphens')
        self.namespace = namespace
        self.head_key = f'{namespace}:head'
        try:
            self.db = FalkorDB(host=host, port=port, socket_connect_timeout=3, socket_timeout=8)
            self.redis = self.db.connection
            self.redis.ping()
            self.redis.execute_command('GRAPH.LIST')
        except RedisError as exc:
            raise MemoryUnavailable('FalkorDB is unavailable; no memory was created') from exc

    def _record(self):
        try:
            head = self.redis.get(self.head_key)
            if head is None:
                raise MemoryUnavailable('Memory has not been seeded')
            value = self.redis.get(f'{self.namespace}:snapshot:{head}')
            if value is None:
                raise MemoryUnavailable('Committed snapshot manifest is unavailable')
            return json.loads(value)
        except RedisError as exc:
            raise MemoryUnavailable('Cannot read committed memory') from exc

    def _publish(self, documents, expected):
        canonical = _canonical(documents)
        if len({d['id'] for d in canonical}) != len(canonical):
            raise ValueError('Document IDs must be unique')
        entities = _entities(canonical)
        semantic = settings().semantic
        try:
            current=self.redis.get(self.head_key)
            if current:
                committed=json.loads(self.redis.get(f'{self.namespace}:snapshot:{current}'))
                if (committed.get('schema_version')==5 and committed['documents']==canonical
                        and committed.get('semantic_requested')==semantic and not committed.get('semantic_error')):
                    return {'snapshot':current,'changed':False}
            if current!=expected:
                raise MemoryConflict('The memory changed; reload the current snapshot before saving')
            source_chunks = []
            for document in canonical:
                for chunk in retrieval.chunks(document):
                    source_chunks.append({**chunk, 'revision': _digest(document)})
            semantic_error = None
            vectors = []
            if semantic and source_chunks:
                try:
                    vectors = [retrieval.embed(c['text']) for c in source_chunks]
                except Exception as exc:
                    semantic_error = type(exc).__name__
            snapshot=_digest({'schema':5,'semantic':semantic,'embedding_model':retrieval.MODEL if vectors else None,'documents':canonical})
            if current==snapshot:
                return {'snapshot':snapshot,'changed':False}
            graph_name = f'{self.namespace}__{snapshot[:16]}__{uuid.uuid4().hex}'
            graph = self.db.select_graph(graph_name)
            graph.query('CREATE (:Snapshot {id:$id})', {'id': snapshot})
            for entity in entities:
                graph.query(
                    f'CREATE (:Entity:{entity["kind"]} {{id:$id,name:$name,kind:$kind,aliases:$aliases}})',
                    entity,
                )
            fact_count = 0
            for document in canonical:
                revision = _digest(document)
                seen = set()
                for fact in document['facts']:
                    fact_id = _digest([document['id'], revision, fact])
                    if fact_id in seen:
                        continue
                    seen.add(fact_id)
                    offset = document['text'].index(fact['quote'])
                    props = {**fact, 'fact_id': fact_id, 'document_id': document['id'],
                             'title': document['title'], 'revision': revision,
                             'start': offset, 'end': offset + len(fact['quote'])}
                    graph.query(
                        'MATCH (a:Entity {id:$subject}), (b:Entity {id:$object}) '
                        f'CREATE (a)-[:{fact["predicate"]} {{fact_id:$fact_id,document_id:$document_id,'
                        'title:$title,revision:$revision,quote:$quote,start:$start,end:$end}]->(b)', props,
                    )
                    fact_count += 1
            if vectors:
                graph.query("CREATE VECTOR INDEX FOR (c:Chunk) ON (c.embedding) OPTIONS {dimension:384, similarityFunction:'cosine'}")
            for i,chunk in enumerate(source_chunks):
                query = 'CREATE (:Chunk {id:$id,document_id:$document_id,title:$title,section:$section,quote:$quote,text:$text,start:$start,end:$end,kind:$kind,revision:$revision'
                params = dict(chunk)
                if vectors:
                    query += ',embedding:vecf32($embedding)'
                    params['embedding'] = vectors[i]
                graph.query(query+'})',params)
            actual = graph.ro_query('MATCH ()-[r]->() RETURN count(r)').result_set[0][0]
            if actual != fact_count:
                raise MemoryUnavailable('Staged graph failed integrity verification')
            record = {'snapshot': snapshot, 'graph': graph_name, 'documents': canonical,
                      'entity_count': len(entities), 'fact_count': fact_count, 'schema_version': 5,
                      'chunk_count': len(source_chunks), 'semantic_model': retrieval.MODEL if vectors else None,
                      'semantic_requested': semantic, 'semantic_error': semantic_error}
            self.redis.set(f'{self.namespace}:snapshot:{snapshot}', _json(record))
            result = self.redis.eval(
                'local h=redis.call("GET",KEYS[1]); '
                'if h==ARGV[2] then return 2 end; '
                'if (h or "")==ARGV[1] then redis.call("SET",KEYS[1],ARGV[2]); return 1 end; return 0',
                1, self.head_key, expected or '', snapshot,
            )
            if result == 0:
                raise MemoryConflict('Another publisher committed first; reload before saving')
            return {'snapshot': snapshot, 'changed': result == 1}
        except RedisError as exc:
            raise MemoryUnavailable('FalkorDB publication failed; the prior head remains committed') from exc

    def ensure_seed(self, path: Path):
        try:
            if self.redis.get(self.head_key):
                record = self._record()
                if record.get('schema_version') != 5 or record.get('semantic_requested') != settings().semantic or record.get('semantic_error'):
                    self._publish([Document.model_validate(d) for d in record['documents']], record['snapshot'])
                return self.overview()
            payload = json.loads(Path(path).read_text())
            values = payload['documents'] if isinstance(payload, dict) else payload
            documents = [Document.model_validate(d) for d in values]
            try:
                self._publish(documents, None)
            except MemoryConflict:
                pass
            return self.overview()
        except RedisError as exc:
            raise MemoryUnavailable('Cannot seed memory') from exc

    def overview(self):
        record = self._record()
        return {key: record[key] for key in ('snapshot', 'entity_count', 'fact_count')} | {'document_count': len(record['documents']), 'chunk_count': record.get('chunk_count',0),
            'semantic_model': record.get('semantic_model'), 'semantic_error': record.get('semantic_error')}

    def documents(self):
        record = self._record()
        return {'snapshot': record['snapshot'], 'documents': record['documents']}

    def entities(self):
        return _entities(self._record()['documents'])

    def replace_document(self, document: Document, expected_snapshot: str | None = None):
        document = Document.model_validate(document.model_dump() if isinstance(document, Document) else document)
        record = self._record()
        docs = {d['id']: Document.model_validate(d) for d in record['documents']}
        docs[document.id] = document
        return self._publish(list(docs.values()), expected_snapshot if expected_snapshot is not None else record['snapshot'])

    def _query(self, record, query, params):
        try:
            if not self.redis.exists(record['graph']):
                raise MemoryUnavailable('Committed graph is unavailable')
            return self.db.select_graph(record['graph']).ro_query(query, params, timeout=2000).result_set
        except RedisError as exc:
            raise MemoryUnavailable('Graph retrieval failed') from exc

    @staticmethod
    def _sequence_query(sequence):
        pattern = '(n0:Entity)'
        constraints = ['n0.id=$seed']
        for i, (predicate, forward) in enumerate(sequence):
            source_types, target_types = PREDICATES[predicate]
            left_types, right_types = (source_types, target_types) if forward else (target_types, source_types)
            pattern += f'-[r{i}:{predicate}]->(n{i+1}:Entity)' if forward else f'<-[r{i}:{predicate}]-(n{i+1}:Entity)'
            constraints.extend([f'n{i}.kind IN {_json(sorted(left_types))}', f'n{i+1}.kind IN {_json(sorted(right_types))}'])
        return f'MATCH p={pattern} WHERE ' + ' AND '.join(constraints) + ' RETURN nodes(p), relationships(p) LIMIT 64'

    def replace_documents(self, documents, expected_snapshot=None):
        record = self._record()
        return self._publish([Document.model_validate(d) for d in documents],
            expected_snapshot if expected_snapshot is not None else record['snapshot'])

    def search(self, question, top_k=5, _record=None):
        if not isinstance(question,str) or not question.strip():
            raise ValueError('Question must contain nonblank text')
        if type(top_k) is not int or not 1<=top_k<=10:
            raise ValueError('Search top_k must be between 1 and 10')
        record = _record or self._record()
        fields = ('id','document_id','title','section','quote','text','start','end','kind','revision')
        rows = [dict(zip(fields,row)) for row in self._query(record,'MATCH (c:Chunk) RETURN '+','.join('c.'+f for f in fields),{})]
        legs = {'keyword':retrieval.bm25(rows,question)}
        query = ''
        semantic_error = record.get('semantic_error')
        if record.get('semantic_model'):
            try:
                vector = retrieval.embed('Represent this sentence for searching relevant passages: '+question)
                query = "CALL db.idx.vector.queryNodes('Chunk','embedding',10,vecf32($vector)) YIELD node,score RETURN node.id,score ORDER BY score ASC"
                matches = self._query(record,query,{'vector':vector})
                # FalkorDB's cosine index yields distance (0 is identical), not similarity.
                cutoff=settings().vector_max_distance
                legs['meaning'] = [id for id,distance in matches if cutoff is None or distance<=cutoff]
            except (MemoryUnavailable,ValueError):
                raise
            except Exception as exc:
                semantic_error = type(exc).__name__
        ranked,found,scores = retrieval.fuse(legs)
        by_id = {r['id']:r for r in rows}
        documents={d['id']:d for d in record['documents']}
        hits = [retrieval.neighboring_context({**by_id[id],'found_by':found[id],'score':scores[id]},
            documents[by_id[id]['document_id']],settings().neighbor_lines) for id in ranked[:top_k]]
        return {'snapshot':record['snapshot'],'hits':hits,'legs':list(legs),
            'semantic_model':record.get('semantic_model'),'semantic_error':semantic_error,'query':query}

    def _with_notes(self,result,record,question):
        search = self.search(question,top_k=5,_record=record)
        known = {(c['document_id'],c['quote']) for c in result['citations']}
        for hit in search['hits']:
            if (hit['document_id'],hit['quote']) in known:
                continue
            known.add((hit['document_id'],hit['quote']))
            result['citations'].append({'id':f'S{len(result["citations"])+1}',
                'document_id':hit['document_id'],'title':hit['title'],'quote':hit['quote'],
                'revision':hit['revision'],'section':hit['section'],'found_by':hit['found_by']})
        result['trace']['note_hits'] = len(search['hits'])
        result['trace']['search_legs'] = search['legs']
        result['trace']['semantic_model'] = search['semantic_model']
        result['trace']['semantic_error'] = search['semantic_error']
        if search['query']:
            result['trace']['query'] += '\n'+search['query']
        return result

    def recall(self, question: str, hops: int = 3, top_k: int = 8, include_notes: bool = True, mode=None):
        """Entity-seeded neighbourhood recall, with the walk executed by FalkorDB.

        Retrieval is not an answer. Conditions remain source text for the answering
        model, rather than being discarded or treated as unconditional edges.
        """
        if not isinstance(question, str) or not question.strip():
            raise ValueError('Question must contain nonblank text')
        mode=mode or settings().recall_mode
        if mode not in {'graph','rag','hybrid'}:raise ValueError('Unknown recall mode')
        if mode=='graph':include_notes=False
        if type(hops) is not int or not 1 <= hops <= 3 or type(top_k) is not int or not 1 <= top_k <= 32:
            raise ValueError('Recall supports 1–3 hops and 1–32 facts')
        started = time.perf_counter()
        record = self._record()
        entities = _entities(record['documents'])
        groups = resolve_mentions(question, entities)
        result = {'question': question, 'status': 'missing', 'mode': 'evidence',
                  'answer': '', 'snapshot': record['snapshot'], 'citations': [],
                  'paths': [], 'candidates': [], 'notes': [],
                  'trace': {'seeds': [], 'query': '', 'retrieval_ms': 0.0,
                            'hop_count': 0, 'facts_returned': 0, 'truncated': False}}
        def finish(status, answer):
            if include_notes and status != 'ambiguous':
                self._with_notes(result,record,question)
                if result['citations'] and status=='missing':
                    status,answer='recalled','Retrieved source passages. Interpret their conditions; related text is not a resolved answer.'
            result['status'], result['answer'] = status, answer
            result['evidence_text'] = compact_evidence(result,max_chars=settings().context_chars)
            result['trace']['retrieval_ms'] = round((time.perf_counter() - started) * 1000, 3)
            return result
        if mode=='rag':return finish('missing','Retrieved note context.')
        if not groups:
            return finish('missing', 'No entity name or alias matched. Name the relevant person, policy, service or project.')
        ambiguous = next((g for g in groups if len(g) > 1), None)
        if ambiguous:
            result['candidates'] = ambiguous
            return finish('ambiguous', 'Several entities match this name. Choose a unique name or entity ID.')
        seeds = [g[0]['id'] for g in groups]
        result['trace']['seeds'] = seeds
        # Document references connect otherwise unrelated neighbourhoods. Rank
        # explicit domain relationships first so nearby link hubs cannot consume
        # the entire fact budget before a three-hop ownership/delegation chain.
        query = (f'MATCH p=(s:Entity)-[*1..{hops}]-(n:Entity) WHERE s.id IN $seeds '
                 'UNWIND relationships(p) AS r WITH r, min(length(p)) AS depth '
                 'RETURN startNode(r), r, endNode(r), depth '
                 "ORDER BY CASE WHEN type(r) = 'REFERENCES' THEN 1 ELSE 0 END, "
                 "CASE WHEN r.quote STARTS WITH '---' THEN 1 ELSE 0 END, depth, "
                 "type(r), startNode(r).name, endNode(r).name, r.fact_id LIMIT $limit")
        result['trace']['query'] = query
        rows = self._query(record, query, {'seeds': seeds, 'limit': top_k + 1})
        result['trace']['truncated'] = len(rows) > top_k
        mentioned = set(seeds)
        for source, edge, target, depth in rows[:top_k]:
            props = edge.properties
            citation=next((c for c in result['citations'] if (c['document_id'],c['quote'],c['revision'])==(props['document_id'],props['quote'],props['revision'])),None)
            cid=citation['id'] if citation else f'S{len(result["citations"])+1}'
            if citation is None:result['citations'].append({'id':cid,'document_id':props['document_id'],
                'title':props['title'],'quote':props['quote'],'revision':props['revision']})
            nodes = [{k: n.properties[k] for k in ('id', 'name', 'kind')} for n in (source, target)]
            mentioned.update(n['id'] for n in nodes)
            result['paths'].append({'nodes': nodes, 'edges': [{'subject': nodes[0]['id'],
                'predicate': edge.relation, 'object': nodes[1]['id'], 'citation_ids': [cid]}]})
            result['trace']['hop_count'] = max(result['trace']['hop_count'], depth)
        seen_notes = set()
        for doc in record['documents']:
            for entity in doc['entities']:
                description = entity.get('description', '')
                key = (entity['id'], description, doc['id'])
                if entity['id'] not in mentioned or not description or key in seen_notes:
                    continue
                seen_notes.add(key)
                citation=next((c for c in result['citations'] if (c['document_id'],c['quote'])==(doc['id'],description)),None)
                cid=citation['id'] if citation else f'S{len(result["citations"])+1}'
                if citation is None:result['citations'].append({'id':cid,'document_id':doc['id'],
                    'title':doc['title'],'quote':description,'revision':_digest(doc)})
                result['notes'].append({'entity_id': entity['id'], 'name': entity['name'],
                    'text': description, 'document_id': doc['id'], 'citation_id': cid})
        result['trace']['facts_returned'] = len(rows[:top_k])
        if not result['citations']:
            return finish('missing', 'No recorded facts or conditions were found for the matched entity.')
        return finish('recalled', 'Retrieved related evidence. This is context, not a resolved answer; apply the source conditions when interpreting it.')

    def ask(self, question: str):
        if not isinstance(question, str) or not question.strip():
            raise ValueError('Question must contain nonblank text')
        started = time.perf_counter()
        record = self._record()
        entities = _entities(record['documents'])
        plan = plan_question(question_shape(question, entities))
        mentions = resolve_mentions(question, entities)
        result = {'question': question, 'status': 'missing', 'mode': 'evidence',
                  'answer': '', 'snapshot': record['snapshot'], 'citations': [], 'paths': [], 'candidates': [],
                  'trace': {'seeds': [], 'query': '', 'retrieval_ms': 0.0, 'hop_count': 0, 'facts_returned': 0}}
        queries = []

        def finish(status, answer):
            result['status'] = status
            result['answer'] = answer
            result['trace']['query'] = '\n'.join(queries)
            result['trace']['retrieval_ms'] = round((time.perf_counter() - started) * 1000, 3)
            return result

        if plan.kind == 'unsupported' or any(e.get('description') for e in entities):
            return self.recall(question)
        if not mentions:
            return finish('missing', 'No known entity matched this question. Use an entity name or alias from the sources.')
        if not supported_shape(question, entities, plan.kind):
            return self.recall(question)
        ambiguous = next((group for group in mentions if len(group) > 1), None)
        if ambiguous:
            result['candidates'] = ambiguous
            return finish('ambiguous', 'This name matches several entities. Rephrase with a unique entity ID or alias.')
        seeds = [group[0] for group in mentions]
        result['trace']['seeds'] = [entity['id'] for entity in seeds]
        if plan.kind == 'connection':
            if len(seeds) != 2:
                return finish('missing', 'A connection question needs two distinct known entities.')
            query = 'MATCH p=(a:Entity)-[*1..3]-(b:Entity) WHERE a.id=$seed AND b.id=$target RETURN nodes(p), relationships(p) LIMIT 64'
            queries.append(query)
            rows = self._query(record, query, {'seed': seeds[0]['id'], 'target': seeds[1]['id']})
        elif plan.kind == 'approver' and len(seeds) == 1:
            queries.append(APPROVER_QUERY)
            rows = self._query(record, APPROVER_QUERY, {'seed': seeds[0]['id']})
        elif plan.kind == 'describe':
            if len(seeds) != 1:
                return finish('unsupported', 'Describe one entity at a time, or ask for a connection between two entities.')
            query = 'MATCH p=(a:Entity)-[r]-(b:Entity) WHERE a.id=$seed RETURN nodes(p), relationships(p) LIMIT 64'
            queries.append(query)
            rows = self._query(record, query, {'seed': seeds[0]['id']})
        else:
            if len(seeds) != 1:
                return finish('unsupported', 'This question form takes one entity. Use its unique name or ID.')
            rows = []
            for sequence in plan.sequences:
                query = self._sequence_query(sequence)
                queries.append(query)
                rows = self._query(record, query, {'seed': seeds[0]['id']})
                if rows:
                    break
        if not rows:
            return finish('missing', 'The committed sources do not contain a complete supporting path for this question.')
        citations = {}
        paths = {}
        names = set()
        statements = set()
        for nodes, edges in rows:
            node_data = [{k: n.properties[k] for k in ('id', 'name', 'kind')} for n in nodes]
            directed_nodes = [(nodes[i], nodes[i+1]) if edge.src_node == nodes[i].id else (nodes[i+1], nodes[i])
                              for i, edge in enumerate(edges)]
            semantic = tuple((source.properties['id'], edge.relation, target.properties['id'])
                             for edge, (source, target) in zip(edges, directed_nodes))
            if semantic not in paths:
                paths[semantic] = {'nodes': node_data, 'edges': []}
                for i, edge in enumerate(edges):
                    source, target = directed_nodes[i]
                    paths[semantic]['edges'].append({'predicate': edge.relation, 'subject': source.properties['id'], 'object': target.properties['id'], 'citation_ids': []})
            for i, edge in enumerate(edges):
                props = edge.properties
                key = props['fact_id']
                if key not in citations:
                    citations[key] = {'id': f'S{len(citations)+1}', 'document_id': props['document_id'],
                                      'title': props['title'], 'quote': props['quote'], 'revision': props['revision']}
                citation_id = citations[key]['id']
                edge_data = paths[semantic]['edges'][i]
                if citation_id not in edge_data['citation_ids']:
                    edge_data['citation_ids'].append(citation_id)
                source, target = directed_nodes[i]
                statements.add(f'{source.properties["name"]} {edge.relation} {target.properties["name"]} [{citation_id}]')
            names.add(node_data[-1]['name'])
        result['citations'] = list(citations.values())
        result['paths'] = list(paths.values())
        result['trace']['hop_count'] = max(len(p['edges']) for p in result['paths'])
        result['trace']['facts_returned'] = len(citations)
        if plan.kind in {'describe', 'connection'}:
            answer = '; '.join(sorted(statements)) + '.'
        else:
            prefix = {'owner': 'Owner', 'leader': 'Team leader', 'policies': 'Governing policies', 'approver': 'Release approver'}[plan.kind]
            answer = f'{prefix}: {", ".join(sorted(names))}. ' + ' '.join(f'[{c["id"]}]' for c in result['citations'])
        if len(rows) == 64:
            answer += ' Showing a bounded selection of up to 64 supporting paths.'
        return finish('supported', answer)

    def save_investigation(self, question: str):
        answer = self.ask(question)
        item = {'id': uuid.uuid4().hex, 'question': question, 'snapshot': answer['snapshot'],
                'created_at': datetime.now(timezone.utc).isoformat()}
        try:
            self.redis.hset(f'{self.namespace}:investigations', item['id'], _json(item))
            return item
        except RedisError as exc:
            raise MemoryUnavailable('Cannot save investigation') from exc

    def investigations(self):
        try:
            values = self.redis.hvals(f'{self.namespace}:investigations')
            return sorted((json.loads(value) for value in values), key=lambda i: i['created_at'], reverse=True)
        except RedisError as exc:
            raise MemoryUnavailable('Cannot list investigations') from exc

    def get_investigation(self, id):
        try:
            value = self.redis.hget(f'{self.namespace}:investigations', id)
            if value is None:
                raise KeyError(id)
            return json.loads(value)
        except RedisError as exc:
            raise MemoryUnavailable('Cannot read investigation') from exc
