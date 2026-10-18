import os
import subprocess
import uuid
from pathlib import Path

from memory_lab.domain import Document
from memory_lab.memory import Memory


def main():
    root = Path(__file__).resolve().parents[1]
    namespace = 'durability_' + uuid.uuid4().hex
    memory = Memory(port=int(os.environ.get('FALKORDB_PORT', '6387')), namespace=namespace)
    memory.ensure_seed(root / 'data' / 'demo.json')
    current = memory.documents()
    document = next(d for d in current['documents'] if d['id'] == 'release-delegation')
    document['text'] = 'Noah delegates to Jun.'
    document['facts'][0].update(object='jun', quote='Noah delegates to Jun.')
    receipt = memory.replace_document(Document.model_validate(document), current['snapshot'])
    saved = memory.save_investigation('Who approves Atlas releases?')
    print('Recreating this project database, retaining its volume.', flush=True)
    subprocess.run(['docker', 'compose', '-p', 'graph-memory-lab', 'up', '-d', '--force-recreate', '--wait'], cwd=root, check=True)
    fresh = Memory(port=int(os.environ.get('FALKORDB_PORT', '6387')), namespace=namespace)
    try:
        result = fresh.ask(fresh.get_investigation(saved['id'])['question'])
        assert result['snapshot'] == receipt['snapshot'], 'The committed snapshot did not survive recreation'
        assert result['status'] == 'supported' and 'Jun' in result['answer'], 'The updated graph did not survive recreation'
        assert any(c['quote'] == 'Noah delegates to Jun.' for c in result['citations']), 'Updated provenance was lost'
        search=fresh.search('Noah delegates to Jun')
        assert any(h['document_id']=='release-delegation' for h in search['hits']), 'Passage index was lost'
        assert search['semantic_model'] and 'meaning' in search['legs'] and not search['semantic_error'], 'Native vector index was lost'
        assert fresh.redis.config_get('appendonly')['appendonly'] == 'yes'
        print('PASS: updated graph, source citation, snapshot, saved investigation, chunks and native vector lookup survived container recreation.')
    finally:
        for name in fresh.redis.execute_command('GRAPH.LIST'):
            if name.startswith(namespace + '__'):
                fresh.db.select_graph(name).delete()
        for key in fresh.redis.scan_iter(namespace + ':*'):
            fresh.redis.delete(key)


if __name__ == '__main__':
    main()
