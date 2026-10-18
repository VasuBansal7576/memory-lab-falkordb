import uuid
from pathlib import Path

import pytest

from memory_lab.domain import Document
from memory_lab.memory import Memory


@pytest.fixture
def memory():
    value = Memory(namespace='test_recall_' + uuid.uuid4().hex)
    value.ensure_seed(Path('data/demo.json'))
    yield value
    for name in value.db.list_graphs():
        if name.startswith(value.namespace + '__'):
            value.db.select_graph(name).delete()
    for key in value.redis.scan_iter(match=value.namespace + ':*'):
        value.redis.delete(key)


def test_paraphrase_uses_native_entity_recall_instead_of_rejecting(memory):
    result = memory.ask('Who should sign off an Atlas release?')
    assert result['status'] == 'recalled'
    assert {c['document_id'] for c in result['citations']} >= {
        'atlas-approval', 'steward-assignment', 'release-delegation'}
    assert 'Mira' in result['evidence_text']
    assert 'MATCH' in result['trace']['query']
    assert result['trace']['hop_count'] == 3
    assert 'Release approver:' not in result['answer']


def test_conditional_notes_have_exact_source_provenance(memory):
    doc = next(d for d in memory.documents()['documents'] if d['id'] == 'release-delegation')
    condition = 'This delegation applies only from 1 to 31 March 2026.'
    doc['text'] += ' ' + condition
    next(e for e in doc['entities'] if e['id'] == 'noah')['description'] = condition
    memory.replace_document(Document.model_validate(doc))
    result = memory.ask('Who approves Atlas releases in April 2026?')
    assert result['status'] == 'recalled'
    assert condition in result['evidence_text']
    assert any(c['quote'] == condition for c in result['citations'])
    assert 'Release approver: Mira' not in result['answer']


def test_recall_is_bounded_and_does_not_inject_a_computed_answer(memory):
    result = memory.recall('Anything relevant to Atlas releases?', top_k=2)
    assert len(result['paths']) <= 2
    assert result['trace']['truncated']
    assert 'Release approver:' not in result['evidence_text']
    assert result['snapshot'] == memory.overview()['snapshot']


def test_ambiguous_and_unmatched_seeds_do_not_guess(memory):
    assert memory.recall('What changed for Alex?')['status'] == 'ambiguous'
    assert memory.recall('What changed for Nimbus?')['status'] == 'missing'


def test_entity_notes_cannot_claim_text_absent_from_source():
    with pytest.raises(ValueError, match='description'):
        Document.model_validate({'id': 'x', 'title': 'X', 'text': 'Mira is on call.',
            'entities': [{'id': 'mira', 'kind': 'Person', 'name': 'Mira',
                          'description': 'Mira approves every payment.'}], 'facts': []})
