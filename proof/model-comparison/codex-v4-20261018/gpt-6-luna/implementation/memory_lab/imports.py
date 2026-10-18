"""Adapters for reference-style modeled Markdown and nodes/edges extraction JSON."""
import json
import re
import uuid
from pathlib import Path

from .domain import Document,Kind,PREDICATES


def normalized(name):return re.sub(r'\s+',' ',str(name).strip()).casefold()


def kind_name(value):
    return next((k for k in Kind.__args__ if k.casefold()==str(value).casefold()),'Document')


def identity(name,kind,known=()):
    matches={e['id']:e for e in known if e['kind']==kind and normalized(name) in
        {normalized(e['name']),*(normalized(a) for a in e.get('aliases',[]))}}
    if len(matches)==1:
        e=next(iter(matches.values()))
        return {'id':e['id'],'name':e['name'],'kind':kind,'aliases':sorted(set(e.get('aliases',[])+[name])-{e['name']})}
    return {'id':str(uuid.uuid5(uuid.NAMESPACE_URL,'memory-lab:'+kind+':'+normalized(name))),
        'name':name,'kind':kind,'aliases':[]}


def reference_extraction(document,payload,known=()):
    """Original paraphrased descriptions are not promoted to verbatim quotations."""
    d=Document.model_validate(document);entities=[];names={}
    by_label={}
    for e in known:
        for label in [e['name'],*e.get('aliases',[])]:by_label.setdefault(normalized(label),{})[e['id']]=e
    def resolve(name):
        key=normalized(name)
        if key in names:return names[key]
        matches=by_label.get(key,{})
        if len(matches)!=1:return None
        e=dict(next(iter(matches.values())));e['description']=''
        names[key]=e
        if not any(n['id']==e['id'] for n in entities):entities.append(e)
        return e
    for node in payload['nodes']:
        kind=kind_name(node['type'])
        if str(node['type']).casefold() not in {k.casefold() for k in Kind.__args__}:raise ValueError('Unknown extraction kind')
        entity=identity(node['name'],kind,known)
        description=node.get('description','')
        entity['description']=description if description in d.text else ''
        names[normalized(node['name'])]=entity;entities.append(entity)
    for alias in payload.get('aliases',[]):
        entity=resolve(alias['entity'])
        if entity is None:raise ValueError('Unknown alias entity')
        entity['aliases']=sorted(set(entity['aliases']+[alias['alias']]))
    # Reference JSON carries a source document rather than per-edge quotations.
    # Retain its complete source body as evidence, never a fabricated paraphrase.
    body=d.text
    if body.startswith('---\n'):
        match=re.match(r'^---\n.*?\n---(?:\n|$)',body,re.S)
        if match:body=body[match.end():]
    facts=[]
    for edge in payload['edges']:
        source=resolve(edge['source']);target=resolve(edge['target'])
        if source is None or target is None:raise ValueError('Unknown extraction endpoint')
        predicate=edge['predicate'].upper()
        if predicate not in PREDICATES:raise ValueError('Unknown extraction predicate')
        quote=edge.get('quote') or body
        if not quote.strip() or len(quote)>4000:raise ValueError('Long extraction sources require explicit edge quotes')
        facts.append({'subject':source['id'],'predicate':predicate,'object':target['id'],'quote':quote})
    return Document.model_validate(d.model_dump()|{'entities':entities,'facts':facts,
        'processed':sorted(set(d.processed)|{'extracted'})})


def extraction_files(folder):
    if folder is None:return {},[]
    root=Path(folder).expanduser().resolve()
    if not root.is_dir():raise ValueError('Extraction folder does not exist')
    values={};errors=[]
    for path in sorted(root.glob('*.json')):
        try:
            value=json.loads(path.read_text())
            source=value['source_doc']
            if not isinstance(source,str) or not source.strip():raise ValueError('Missing extraction source')
            for field,required in [('nodes',['name','type']),('edges',['source','predicate','target']),('aliases',['entity','alias'])]:
                entries=value.get(field,[])
                if not isinstance(entries,list):raise ValueError('Invalid extraction collection')
                if any(not isinstance(e,dict) or any(not isinstance(e.get(k),str) or not e[k].strip() for k in required) for e in entries):
                    raise ValueError('Invalid extraction entry')
            if source in values:raise ValueError('Duplicate extraction source')
            values[source]=value
        except (ValueError,KeyError,TypeError) as exc:
            errors.append({'path':str(path),'stage':'extraction-file','error':type(exc).__name__})
    return values,errors


def extraction_entities(payloads,known=()):
    """Reference exports resolve endpoints across documents in a global first pass."""
    pool={e['id']:dict(e) for e in known}
    for payload in payloads.values():
        for node in payload.get('nodes',[]):
            kind=kind_name(node.get('type'))
            if str(node.get('type')).casefold() not in {k.casefold() for k in Kind.__args__}:continue
            if not isinstance(node.get('name'),str) or not node['name'].strip():continue
            e=identity(node['name'],kind,pool.values());pool.setdefault(e['id'],e)
    for payload in payloads.values():
        for alias in payload.get('aliases',[]):
            candidates=[e for e in pool.values() if normalized(e['name'])==normalized(alias.get('entity'))]
            if len(candidates)==1 and isinstance(alias.get('alias'),str):
                e=candidates[0];e['aliases']=sorted(set(e.get('aliases',[])+[alias['alias']]))
    return list(pool.values())
