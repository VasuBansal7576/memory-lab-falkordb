import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError

from memory_lab import Document, Memory, MemoryConflict, MemoryUnavailable

FIXTURE = Path(__file__).resolve().parents[1] / 'data/demo.json'


@pytest.fixture
def memory():
    namespace = 'test_memory_' + uuid.uuid4().hex
    memory = Memory(namespace=namespace)
    memory.ensure_seed(FIXTURE)
    yield memory
    for key in memory.redis.scan_iter(match=f'{namespace}*'):
        if key.startswith(namespace + '__'):
            memory.db.select_graph(key).delete()
        else:
            memory.redis.delete(key)


def document(memory, id):
    return next(d for d in memory.documents()['documents'] if d['id'] == id)


def replace(memory, data, expected=None):
    return memory.replace_document(Document.model_validate(data), expected)


@pytest.mark.parametrize(('question', 'name', 'hops'), [
    ('Who owns Beacon?', 'Platform', 1),
    ('Who leads the team that owns Beacon?', 'Elena', 2),
    ('Which policies govern services used by Atlas?', 'Retention', 2),
    ('Who approves Atlas releases?', 'Mira', 3),
])
def test_source_backed_multihop(memory, question, name, hops):
    answer = memory.ask(question)
    assert answer['status'] == 'supported'
    assert name in answer['answer']
    assert answer['trace']['hop_count'] == hops
    assert len(answer['citations']) == hops
    assert answer['mode'] == 'evidence'
    sources = {d['id']: d for d in memory.documents()['documents']}
    for citation in answer['citations']:
        assert citation['quote'] in sources[citation['document_id']]['text']
        assert len(citation['revision']) == 64
    assert '$seed' in answer['trace']['query']


def test_direction_alias_and_native_paths(memory):
    answer = memory.ask('Who owns Beacon service?')
    assert answer['status'] == 'supported'
    assert answer['paths'][0]['edges'][0]['subject'] == 'platform'
    assert answer['paths'][0]['edges'][0]['object'] == 'beacon'
    path = memory.ask('Show the connection between Elena Park and Beacon')
    assert path['status'] == 'supported'
    assert len(path['paths'][0]['edges']) == 2
    assert 'LEADS' in path['answer'] and 'OWNS' in path['answer']
    assert memory.ask('Who owns Platform?')['status'] == 'missing'
    assert memory.ask('Connection between Mira and Atlas releases')['status'] == 'supported'
    assert memory.ask('Connection between Mira and Atlas')['status'] == 'missing'


def test_ambiguity_missing_and_unsupported(memory):
    ambiguous = memory.ask('Describe Alex')
    assert ambiguous['status'] == 'ambiguous'
    assert {c['id'] for c in ambiguous['candidates']} == {'alex-design', 'alex-ops'}
    assert not ambiguous['citations']
    assert memory.ask('Describe Alex Morgan')['status'] == 'missing'
    assert memory.ask('Who owns UnknownThing?')['status'] == 'missing'
    assert memory.ask('Predict next quarter revenue for Atlas')['status'] == 'recalled'
    assert memory.ask('Who approves Atlas releases?')['trace']['seeds'] == ['atlas-releases']


def test_update_is_atomic_persisted_and_idempotent(memory):
    before = memory.overview()
    data = document(memory, 'release-delegation')
    data['text'] = 'Noah delegates to Jun. Mira has no current delegation.'
    data['facts'] = [{'subject': 'noah', 'predicate': 'DELEGATES_TO', 'object': 'jun', 'quote': 'Noah delegates to Jun.'}]
    saved = replace(memory, data, before['snapshot'])
    assert saved['changed'] and saved['snapshot'] != before['snapshot']
    answer = memory.ask('Who approves Atlas releases?')
    assert 'Jun' in answer['answer'] and 'Mira' not in answer['answer']
    assert all('Mira' not in c['quote'] for c in answer['citations'])
    assert replace(memory, data)['changed'] is False
    assert memory.overview()['fact_count'] == before['fact_count']
    restored = Memory(namespace=memory.namespace)
    assert restored.overview()['snapshot'] == saved['snapshot']
    assert 'Jun' in restored.ask('Who approves Atlas releases?')['answer']


def test_missing_essential_edge_abstains_and_optional_delegate_falls_back(memory):
    delegation = document(memory, 'release-delegation')
    delegation['facts'] = []
    replace(memory, delegation)
    assert 'Noah' in memory.ask('Who approves Atlas releases?')['answer']
    assignment = document(memory, 'steward-assignment')
    assignment['facts'] = []
    replace(memory, assignment)
    answer = memory.ask('Who approves Atlas releases?')
    assert answer['status'] == 'missing' and answer['citations'] == []
    assert 'Platform' in memory.ask('Who owns Beacon?')['answer']


def test_replacing_document_preserves_independent_support(memory):
    independent = document(memory, 'beacon-ownership')
    independent['id'] = 'independent-ownership'
    independent['title'] = 'Second ownership source'
    replace(memory, independent)
    answer = memory.ask('Who owns Beacon?')
    assert len(answer['paths']) == 1
    assert len(answer['paths'][0]['edges'][0]['citation_ids']) == 2
    assert len(answer['citations']) == 2
    original = document(memory, 'beacon-ownership')
    original['facts'] = []
    replace(memory, original)
    answer = memory.ask('Who owns Beacon?')
    assert answer['status'] == 'supported'
    assert [c['document_id'] for c in answer['citations']] == ['independent-ownership']


@pytest.mark.parametrize('mutation', ['quote', 'unknown', 'types', 'predicate', 'empty'])
def test_invalid_document_never_changes_head(memory, mutation):
    before = memory.overview()
    data = document(memory, 'beacon-ownership')
    if mutation == 'quote':
        data['facts'][0]['quote'] = 'Platform probably owns Beacon.'
    elif mutation == 'unknown':
        data['facts'][0]['object'] = 'missing-id'
    elif mutation == 'types':
        data['entities'][0]['kind'] = 'Person'
    elif mutation == 'predicate':
        data['facts'][0]['predicate'] = 'INVENTED'
    else:
        data['text'] = ' '
    with pytest.raises((ValidationError, ValueError)):
        replace(memory, data)
    assert memory.overview() == before


def test_conflicting_identity_rejected_before_publication(memory):
    before = memory.overview()
    data = document(memory, 'beacon-ownership')
    next(e for e in data['entities'] if e['id'] == 'beacon')['name'] = 'Impostor'
    with pytest.raises(ValueError, match='conflicting identity'):
        replace(memory, data)
    assert memory.overview() == before


def test_stale_snapshot_conflict(memory):
    stale = memory.overview()['snapshot']
    data = document(memory, 'platform-leadership')
    data['title'] = 'Updated leadership memo'
    replace(memory, data, stale)
    other = document(memory, 'beacon-ownership')
    other['title'] = 'Outdated writer'
    with pytest.raises(MemoryConflict):
        replace(memory, other, stale)
    assert document(memory, 'beacon-ownership')['title'] != 'Outdated writer'


def test_concurrent_publishers_cannot_lose_update(memory):
    head = memory.overview()['snapshot']
    first = document(memory, 'beacon-ownership')
    second = document(memory, 'platform-leadership')
    first['title'] = 'Concurrent ownership'
    second['title'] = 'Concurrent leadership'

    def publish(data):
        try:
            return replace(Memory(namespace=memory.namespace), data, head)['changed']
        except MemoryConflict:
            return 'conflict'

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(publish, [first, second]))
    assert sorted(map(str, results)) == ['True', 'conflict']
    assert memory.overview()['document_count'] == 10


def test_investigations_persist_question_and_snapshot(memory):
    saved = memory.save_investigation('Who approves Atlas releases?')
    restored = Memory(namespace=memory.namespace)
    assert restored.get_investigation(saved['id']) == saved
    assert restored.investigations() == [saved]
    with pytest.raises(KeyError):
        restored.get_investigation('not-found')


def test_bound_and_quote_offsets(memory):
    answer = memory.ask('Describe Beacon')
    assert answer['status'] == 'supported'
    assert all(len(p['edges']) <= 3 for p in answer['paths'])
    record = memory._record()
    rows = memory.db.select_graph(record['graph']).ro_query(
        'MATCH ()-[r]->() RETURN r.document_id,r.quote,r.start,r.end'
    ).result_set
    sources = {d['id']: d['text'] for d in record['documents']}
    for id, quote, start, end in rows:
        assert sources[id][start:end] == quote


def test_unavailable_db_fails_without_creating_memory():
    with pytest.raises(MemoryUnavailable):
        Memory(port=1, namespace='unavailable_test')


@pytest.mark.parametrize('question', [
    'Who owns Nimbus besides Beacon?',
    'Who owns Beacon and Nimbus?',
    'Who approves Atlas releases next year?',
    'Who owns Beacon if Platform closes?',
    'Who does not own Beacon?',
    'Who owns Beacon in 2027?',
    'Who owns Beacon and what is its revenue?',
])
def test_unknown_slots_and_unmodeled_conditions_are_not_answered(memory, question):
    answer = memory.ask(question)
    assert answer['status'] == 'recalled'
    assert 'not a resolved answer' in answer['answer']
    assert answer['citations']


def test_delegation_preference_is_per_role_holder(memory):
    data = document(memory, 'steward-assignment')
    jun = next(e for e in document(memory, 'release-delegation')['entities'] if e['id'] == 'jun')
    data['entities'].append(jun)
    data['text'] += ' Release Steward is also held by Jun.'
    data['facts'].append({'subject': 'release-steward', 'predicate': 'HELD_BY', 'object': 'jun', 'quote': 'Release Steward is also held by Jun.'})
    replace(memory, data)
    answer = memory.ask('Who approves Atlas releases?')
    assert answer['status'] == 'supported'
    assert 'Mira' in answer['answer'] and 'Jun' in answer['answer']
    assert 'Noah' not in answer['answer']
    assert sorted(len(p['edges']) for p in answer['paths']) == [2, 3]


def test_large_result_is_bounded_and_disclosed(memory):
    beacon = next(e for e in document(memory, 'beacon-ownership')['entities'] if e['id'] == 'beacon')
    entities = [beacon]
    facts = []
    quotes = []
    for i in range(70):
        entities.append({'id': f'team-{i}', 'kind': 'Team', 'name': f'Team {i}', 'aliases': []})
        quote = f'Team {i} owns Beacon.'
        quotes.append(quote)
        facts.append({'subject': f'team-{i}', 'predicate': 'OWNS', 'object': 'beacon', 'quote': quote})
    replace(memory, {'id': 'many-owners', 'title': 'Large support set', 'text': ' '.join(quotes), 'entities': entities, 'facts': facts})
    answer = memory.ask('Who owns Beacon?')
    assert answer['status'] == 'supported'
    assert len(answer['paths']) <= 64 and len(answer['citations']) <= 64
    assert 'bounded selection' in answer['answer']


def test_reordering_source_data_is_idempotent(memory):
    data = document(memory, 'release-delegation')
    data['entities'].reverse()
    data['facts'].reverse()
    assert replace(memory, data)['changed'] is False


def test_opposite_direction_facts_keep_distinct_paths_and_citations(memory):
    entities = [{'id': name, 'kind': 'Service', 'name': name.upper(), 'aliases': []} for name in ('x', 'y')]
    for id, subject, object in [('forward', 'x', 'y'), ('reverse', 'y', 'x'), ('parallel', 'x', 'y')]:
        quote = f'{subject.upper()} depends on {object.upper()}.'
        replace(memory, {'id': id, 'title': id, 'text': quote, 'entities': entities,
                         'facts': [{'subject': subject, 'predicate': 'DEPENDS_ON', 'object': object, 'quote': quote}]})
    answer = memory.ask('Connection between X and Y')
    assert answer['status'] == 'supported'
    direct_paths = [path for path in answer['paths'] if len(path['edges']) == 1]
    assert len(direct_paths) == 2
    citations = {c['id']: c for c in answer['citations']}
    supports = {}
    for path in direct_paths:
        edge = path['edges'][0]
        supports[(edge['subject'], edge['object'])] = len(edge['citation_ids'])
        for id in edge['citation_ids']:
            assert citations[id]['quote'] == f'{edge["subject"].upper()} depends on {edge["object"].upper()}.'
    assert supports == {('x', 'y'): 2, ('y', 'x'): 1}


@pytest.mark.parametrize('direct_id', ['aaa-direct', 'zzz-direct'])
def test_direct_approvals_and_role_approvals_apply_each_person_delegation(memory, direct_id):
    policy = next(e for e in document(memory, 'atlas-approval')['entities'] if e['kind'] == 'Policy')
    delegation = document(memory, 'release-delegation')
    people = {e['id']: e for e in delegation['entities']}
    quote = 'Atlas releases is approved by Jun and Noah.'
    replace(memory, {'id': direct_id, 'title': 'Direct approval', 'text': quote,
                     'entities': [policy, people['jun'], people['noah']],
                     'facts': [{'subject': policy['id'], 'predicate': 'APPROVED_BY', 'object': person, 'quote': quote}
                               for person in ('jun', 'noah')]})
    answer = memory.ask('Who approves Atlas releases?')
    assert answer['status'] == 'supported'
    assert 'Jun' in answer['answer'] and 'Mira' in answer['answer'] and 'Noah' not in answer['answer']
    assert sorted(len(p['edges']) for p in answer['paths']) == [1, 2, 3]
    role_policy = document(memory, 'atlas-approval')
    role_policy['facts'] = []
    replace(memory, role_policy)
    direct = memory.ask('Who approves Atlas releases?')
    assert direct['status'] == 'supported'
    assert sorted(len(p['edges']) for p in direct['paths']) == [1, 2]


@pytest.mark.parametrize('name', ['Approval', 'Owner', 'Policy', 'Connection', 'Leader'])
def test_question_grammar_masks_entity_keywords_before_planning(memory, name):
    quote = f'Incident Team owns {name}.'
    replace(memory, {'id': 'keyword-name', 'title': 'Keyword entity', 'text': quote,
                     'entities': [{'id': 'keyword-service', 'kind': 'Service', 'name': name, 'aliases': []},
                                  {'id': 'incident-team', 'kind': 'Team', 'name': 'Incident Team', 'aliases': []}],
                     'facts': [{'subject': 'incident-team', 'predicate': 'OWNS', 'object': 'keyword-service', 'quote': quote}]})
    answer = memory.ask(f'Describe {name}')
    assert answer['status'] == 'supported'
    assert answer['citations'][0]['quote'] == quote
