"""Exercise the original raw-note and distilled DATA with the independent RAG."""
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
from scripts.verify_reference_import import PIN,BASE


def main():
    tree=json.load(urllib.request.urlopen('https://api.github.com/repos/Glitch-Cat-Club/graph-memory-starter/git/trees/'+PIN+'?recursive=1'))['tree']
    paths=[x['path'] for x in tree if x['type']=='blob' and x['path'].endswith('.md') and
        (x['path'].startswith('corpus-before/') or x['path'].startswith('rag/distilled/'))]
    os.environ['MEMORY_SEMANTIC']='1';memory=Memory(namespace='rag_reference_'+uuid.uuid4().hex[:12])
    try:
        with tempfile.TemporaryDirectory(prefix='reference-rag-') as tmp:
            root=Path(tmp);(root/'empty.json').write_text('[]');memory.ensure_seed(root/'empty.json')
            def fetch(path):return path,urllib.request.urlopen(BASE+path).read()
            with ThreadPoolExecutor(max_workers=4) as executor:
                for path,data in executor.map(fetch,paths):
                    file=root/path;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(data)
            imported=sync_notes(memory,root/'corpus-before',distilled=root/'rag/distilled')
            assert not imported['errors'],imported['errors']
            documents=memory.documents()['documents'];by_id={d['id']:d for d in documents}
            count=sum(len(d['distilled']) for d in documents)
            assert imported['updated']==12 and count==5,(imported,count)
            cases=[('what is the mileage rate',['expenses.md'],None),
                ('how do we talk to customers in writing',['customer-ops-handbook.md','brand-voice.md'],None),
                ('who has sign-off while Sarah is away',['ooo-march-sarah.md'],'distilled'),
                ('A customer wants an £800 refund in March. Who signs it off?',['customer-ops-handbook.md'],None)]
            rows=[]
            for question,expected,kind in cases:
                result=memory.search(question,top_k=5)
                hits=[{'source':by_id[h['document_id']]['origin'].split('/',1)[1],
                    'kind':h['kind'],'quote':h['quote'],'matched_quote':h.get('matched_quote'),
                    'found_by':h['found_by'],'score':h['score']} for h in result['hits']]
                assert result['legs']==['keyword','meaning'] and not result['semantic_error']
                assert hits[0]['source'] in expected,(question,hits)
                if kind:assert hits[0]['kind']==kind,(question,hits)
                rows.append({'question':question,'expected_first_source_any':expected,'expected_first_kind':kind,'hits':hits})
            assert not sync_notes(memory,root/'corpus-before',distilled=root/'rag/distilled')['changed']
            receipt={'status':'passed','created_at':datetime.now(timezone.utc).isoformat(),
                'reference_commit':PIN,'scope':'Twelve original raw notes plus three original distilled sidecars. First-hit source and specified distilled-kind checks, not generated-answer accuracy.',
                'source_count':12,'sidecar_count':3,'distilled_question_count':count,'cases':rows,
                'source_urls':[BASE+p for p in paths],
                'reference_data_license':{'url':BASE+'LICENSE','notice':urllib.request.urlopen(BASE+'LICENSE').read().decode()}}
            out=ROOT/'proof/reference-parity/reference-rag.json';out.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
            print(json.dumps({'status':'passed','sources':12,'sidecars':3,'cases':4,'receipt':str(out)}))
    finally:
        for name in memory.db.list_graphs():
            if name.startswith(memory.namespace+'__'):memory.db.select_graph(name).delete()
        for key in memory.redis.scan_iter(match=memory.namespace+':*'):memory.redis.delete(key)


if __name__=='__main__':main()
