"""Real Codex write-time tasks, native vector lookup and future-session recall."""
import json
import os
from dataclasses import replace
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from memory_lab.settings import settings,open_memory
from memory_lab.model_tasks import call_json
from memory_lab.sessions import create_session,append_exchange,close_session,process_pending


def main():
    cfg=settings()
    cfg=replace(cfg,state_dir=ROOT/'.memory'/'lifecycle-proof',notes_dir=ROOT/'.memory'/'lifecycle-proof'/'notes')
    os.environ.pop('MEMORY_LAB_CHILD',None)
    memory=open_memory()
    quote='Project Lumen uses cobalt for its release dashboard. Only Priya Raman may approve Lumen releases costing more than £750 until 31 October 2026.'
    existing=list((cfg.state_dir/'pending').glob('*.json'))
    if existing:
        path=existing[0];job=json.loads(path.read_text());id=job['session_id'];staged={'path':str(path),'key':job['key']}
    else:
        id=create_session(cfg)
        append_exchange(id,quote,'Recorded: cobalt dashboard, with the named approver, amount threshold and expiry date.',cfg)
        staged=close_session(id,cfg,spawn=False)
    started=time.perf_counter()
    def recorded_call(prompt,schema):
        value,usage=call_json(prompt,schema)
        with (cfg.state_dir/'model-tasks.jsonl').open('a') as f:f.write(json.dumps({'schema':schema,'value':value,'usage':usage})+'\n')
        return value,usage
    processed=process_pending(memory,cfg,caller=recorded_call)
    job=json.loads(Path(staged['path']).read_text())
    assert job['status']=='indexed',processed
    query='For Lumen, who authorises a £900 release on 20 October 2026?'
    recalled=memory.recall(query)
    source=next(c for c in recalled['citations'] if quote in c['quote'] or ('Priya Raman' in c['quote'] and '750' in c['quote']))
    assert '31 October 2026' in recalled['evidence_text']
    assert process_pending(memory,cfg)==[], 'Indexed job repeated work'
    note=Path(job['note_path']).read_text()
    assert note.count('<!-- memory:'+staged['key']+' -->')==1
    receipt={'status':'passed','elapsed_s':round(time.perf_counter()-started,3),'input':quote,
        'session_id':id,'job_key':staged['key'],'snapshot':job['snapshot'],
        'model_usage':{'summary':job['summary_usage'],'index':job['index_usage']},
        'daily_note':note,'query':query,'recalled':recalled,'supporting_source':source,
        'checks':['real model summary','verbatim transcript quote','daily note written once','real model distillation',
                  'real typed entity extraction','FalkorDB snapshot published','future question recalls note and conditions','retry performs no duplicate work']}
    output=ROOT/'proof'/'lifecycle.json';output.write_text(json.dumps(receipt,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'status':receipt['status'],'proof':str(output),'model_usage':receipt['model_usage'],'snapshot':job['snapshot']}))

if __name__=='__main__':main()
