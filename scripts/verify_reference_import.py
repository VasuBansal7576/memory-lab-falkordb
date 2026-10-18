"""Test pinned upstream example DATA against the independently built importer.

Only Markdown/JSON fixtures are downloaded into a temporary folder; no source code
is copied, cloned or forked. The fixture directory and test graph are removed.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
import json
import os
import sys
import tempfile
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from memory_lab.memory import Memory
from memory_lab.notes import sync_notes

PIN='496a6e9d9b9578943ec5ed34c2780de5a0fa5510'
BASE='https://raw.githubusercontent.com/Glitch-Cat-Club/graph-memory-starter/'+PIN+'/'


def main():
    api='https://api.github.com/repos/Glitch-Cat-Club/graph-memory-starter/git/trees/'+PIN+'?recursive=1'
    tree=json.load(urllib.request.urlopen(api))['tree']
    paths=[x['path'] for x in tree if x['type']=='blob' and
        ((x['path'].startswith('corpus/') and x['path'].endswith('.md')) or
         (x['path'].startswith('extraction/') and x['path'].endswith('.json')))]
    os.environ['MEMORY_SEMANTIC']='0';memory=Memory(namespace='reference_import_'+uuid.uuid4().hex[:12])
    try:
        with tempfile.TemporaryDirectory(prefix='reference-fixtures-') as tmp:
            root=Path(tmp);(root/'empty.json').write_text('[]');memory.ensure_seed(root/'empty.json')
            def fetch(path):return path,urllib.request.urlopen(BASE+path).read()
            with ThreadPoolExecutor(max_workers=4) as executor:
                for path,data in executor.map(fetch,paths):
                    file=root/path;file.parent.mkdir(exist_ok=True);file.write_bytes(data)
            imported=sync_notes(memory,root/'corpus',extractions=root/'extraction')
            assert not imported['errors'],imported['errors']
            cases=[('A customer wants an £800 refund in March. Who signs it off?',['Marcus Webb','DELEGATES_TO']),
                ('Who approves supplier payments over £2,000?',['Alex Doyle','APPROVED_BY']),
                ('What is the onboarding process?',['Ops Manager','Day one']),
                ('What does Priya do?',['Support Lead','Priya Patel'])]
            rows=[]
            for question,required in cases:
                evidence=memory.recall(question,mode='graph')
                assert evidence['status']=='recalled',(question,evidence['status'],evidence.get('candidates'))
                assert all(word in evidence['evidence_text'] for word in required),(question,evidence['evidence_text'])
                rows.append({'question':question,'required_context':required,'evidence':evidence})
            missing=memory.recall('Who is in charge when the boss is away?',mode='graph')
            assert missing['status']=='missing' and not missing['citations']
            repeated=sync_notes(memory,root/'corpus',extractions=root/'extraction')
            assert not repeated['changed'],'Unchanged reference import changed the published graph'
            receipt={'status':'passed','created_at':datetime.now(timezone.utc).isoformat(),'reference_commit':PIN,
                'scope':'Pinned original data compatibility; no upstream source code copied. Tests retrieved facts, not a new model-tier benchmark.',
                'reference_data_license':{'url':BASE+'LICENSE','notice':urllib.request.urlopen(BASE+'LICENSE').read().decode()},
                'source_urls':[BASE+p for p in paths],'document_count':imported['updated'],'cases':rows,
                'unmatched_case':missing,'reimport_changed':repeated['changed']}
            output=ROOT/'proof/reference-parity/reference-import.json';output.parent.mkdir(parents=True,exist_ok=True)
            output.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps({'status':'passed','source_count':imported['updated'],'cases':len(rows)+1,'receipt':str(output)}))
    finally:
        for name in memory.db.list_graphs():
            if name.startswith(memory.namespace+'__'):memory.db.select_graph(name).delete()
        for key in memory.redis.scan_iter(match=memory.namespace+':*'):memory.redis.delete(key)


if __name__=='__main__':main()
