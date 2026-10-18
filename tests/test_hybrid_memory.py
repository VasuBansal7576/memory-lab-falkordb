import json
import uuid
from pathlib import Path

import pytest

from memory_lab.memory import Memory
from memory_lab.notes import sync_notes
from memory_lab.settings import settings


@pytest.fixture
def memory():
    m=Memory(namespace='test_hybrid_'+uuid.uuid4().hex)
    m.ensure_seed(Path('data/operations.json'))
    yield m
    for name in m.db.list_graphs():
        if name.startswith(m.namespace+'__'):m.db.select_graph(name).delete()
    for key in m.redis.scan_iter(match=m.namespace+':*'):m.redis.delete(key)


def test_unannotated_sources_are_recallable(memory):
    for q,id in [('What is the onboarding process?','onboarding'),('How do we handle employee expenses?','expense-guide')]:
        a=memory.recall(q)
        assert a['status']=='recalled'
        assert id in {c['document_id'] for c in a['citations']}
        assert a['trace']['search_legs']==['keyword']


def test_markdown_sync_is_grounded_idempotent_and_removes_deleted_sources(memory,tmp_path):
    notes=tmp_path/'notes';notes.mkdir();(notes/'nested').mkdir()
    file=notes/'nested'/'travel.md';file.write_text('# Travel reimbursements\nStaff may claim train fares with receipts.\n')
    (notes/'.hidden.md').write_text('Do not index this secret marker.')
    first=sync_notes(memory,notes)
    assert first['updated']==1
    result=memory.search('travel train fares')
    hit=result['hits'][0]
    assert hit['quote'] in file.read_text()
    assert sync_notes(memory,notes)['changed'] is False
    file.unlink();assert sync_notes(memory,notes)['removed']==1
    assert all('train fares' not in h['quote'] for h in memory.search('train fares')['hits'])


def test_real_semantic_vectors_are_queried_in_falkordb(memory,monkeypatch):
    monkeypatch.setenv('MEMORY_SEMANTIC','1')
    memory.ensure_seed(Path('data/operations.json'))
    result=memory.search('How can employees get reimbursed for their work journeys?')
    assert result['semantic_model']=='BAAI/bge-small-en-v1.5'
    assert 'meaning' in result['legs']
    assert 'db.idx.vector.queryNodes' in result['query']
    assert any(h['document_id']=='expense-guide' and 'meaning' in h['found_by'] for h in result['hits'])
    assert result['semantic_error'] is None


def test_shared_settings_honor_env_file(monkeypatch,tmp_path):
    for key in ['MEMORY_NAMESPACE','FALKORDB_PORT','MEMORY_STATE_DIR']:
        monkeypatch.delenv(key,raising=False)
    env=tmp_path/'memory.env';env.write_text('MEMORY_NAMESPACE=custom_workspace\nFALKORDB_PORT=6380\nMEMORY_STATE_DIR='+str(tmp_path/'state'))
    cfg=settings(env)
    assert cfg.namespace=='custom_workspace' and cfg.port==6380
    assert cfg.notes_dir==tmp_path/'state'/'notes'
