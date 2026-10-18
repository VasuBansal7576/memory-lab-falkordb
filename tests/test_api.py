import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from memory_lab.app import create_app
from memory_lab.memory import Memory


@pytest.fixture
def client():
    namespace = 'api_test_' + uuid.uuid4().hex
    memory = Memory(port=int(os.environ.get('FALKORDB_PORT', '6387')), namespace=namespace)
    memory.ensure_seed(Path(__file__).resolve().parents[1] / 'data' / 'demo.json')
    with TestClient(create_app(memory, seed=False)) as app_client:
        yield app_client
    for name in memory.redis.execute_command('GRAPH.LIST'):
        if name.startswith(namespace + '__'):
            memory.db.select_graph(name).delete()
    for key in memory.redis.scan_iter(namespace + ':*'):
        memory.redis.delete(key)


def test_full_api_source_update_and_saved_investigation(client):
    initial = client.post('/api/ask', json={'question': 'Who approves Atlas releases?'}).json()
    assert initial['status'] == 'supported'
    assert 'Mira' in initial['answer']
    assert len(initial['paths'][0]['edges']) == 3
    saved = client.post('/api/investigations', json={'question': initial['question']}).json()
    current = client.get('/api/documents').json()
    document = next(d for d in current['documents'] if d['id'] == 'release-delegation')
    document['text'] = 'Noah delegates to Jun.'
    document['facts'][0]['object'] = 'jun'
    document['facts'][0]['quote'] = 'Noah delegates to Jun.'
    changed = client.put('/api/documents/release-delegation', json={
        'document': document, 'expected_snapshot': current['snapshot'],
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()['changed']
    resumed = client.post('/api/investigations/' + saved['id'] + '/resume', json={}).json()
    assert resumed['status'] == 'supported'
    assert 'Jun' in resumed['answer'] and 'Mira' not in resumed['answer']
    assert resumed['resumed_from_snapshot'] == initial['snapshot']
    assert resumed['snapshot'] != initial['snapshot']
    assert any(c['quote'] == 'Noah delegates to Jun.' for c in resumed['citations'])
    replay = client.put('/api/documents/release-delegation', json={
        'document': document, 'expected_snapshot': changed.json()['snapshot'],
    })
    assert replay.json()['changed'] is False


def test_input_validation_cannot_publish_partial_evidence(client):
    current = client.get('/api/documents').json()
    document = current['documents'][0]
    document['facts'][0]['quote'] = 'A claim absent from the source.'
    response = client.put('/api/documents/' + document['id'], json={
        'document': document, 'expected_snapshot': current['snapshot'],
    })
    assert response.status_code == 422
    assert client.get('/api/status').json()['snapshot'] == current['snapshot']
    assert client.post('/api/ask', json={'question': '   '}).status_code == 422
    assert client.post('/api/ask', json={'question': 'x', 'mode': 'invented'}).status_code == 422
    assert client.post('/api/investigations/not-present/resume', json={}).status_code == 404


def test_api_explicitly_exposes_evidence_mode(client, monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    monkeypatch.setattr('memory_lab.app.codex_configuration', lambda: {'configured': False, 'model': None})
    status = client.get('/api/status').json()
    assert status['database'] == 'connected'
    assert status['answer_modes'] == ['evidence']
    assert status['model']['configured'] is False
    result = client.post('/api/ask', json={'question': 'Who owns Beacon?'}).json()
    assert result['mode'] == 'evidence'
    assert 'usage' not in result
    assert client.post('/api/ask', json={'question': 'Who owns Beacon?', 'mode': 'anthropic'}).status_code == 502


def test_optimistic_revision_rejects_lost_updates(client):
    current = client.get('/api/documents').json()
    document = current['documents'][0]
    document['title'] += ' updated'
    assert client.put('/api/documents/' + document['id'], json={
        'document': document, 'expected_snapshot': current['snapshot'],
    }).status_code == 200
    document['title'] += ' stale edit'
    stale = client.put('/api/documents/' + document['id'], json={
        'document': document, 'expected_snapshot': current['snapshot'],
    })
    assert stale.status_code == 409


def test_database_unavailable_at_startup_recovers_without_server_restart(monkeypatch):
    namespace = 'startup_test_' + uuid.uuid4().hex
    monkeypatch.setenv('FALKORDB_PORT', '1')
    monkeypatch.setenv('MEMORY_NAMESPACE', namespace)
    with TestClient(create_app()) as client:
        assert client.get('/').status_code == 200
        assert client.get('/api/status').status_code == 503
        assert client.post('/api/ask', json={'question': 'Who owns Beacon?'}).status_code == 503
        monkeypatch.setenv('FALKORDB_PORT', '6387')
        assert client.get('/api/status').json()['database'] == 'connected'
        result = client.post('/api/ask', json={'question': 'Who owns Beacon?'}).json()
        assert result['status'] == 'recalled' and 'Platform' in result['evidence_text']
    memory = Memory(port=6387, namespace=namespace)
    for name in memory.redis.execute_command('GRAPH.LIST'):
        if name.startswith(namespace + '__'):
            memory.db.select_graph(name).delete()
    for key in memory.redis.scan_iter(namespace + ':*'):
        memory.redis.delete(key)


def test_general_recall_endpoint_preserves_native_query_and_provenance(client):
    result = client.post('/api/recall', json={'question': 'Who should sign off an Atlas release?'}).json()
    assert result['status'] == 'recalled'
    assert 'Mira' in result['evidence_text']
    assert 'UNWIND' in result['trace']['query']
    assert client.post('/api/recall', json={'question': 'x', 'hops': 50}).status_code == 422
