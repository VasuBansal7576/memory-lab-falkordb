"""Exercise real hook subprocesses, detached worker, model writes and later recall.

Hook payloads are synthetic protocol fixtures, not a claim of trusted native UI
execution. Raw receipts distinguish this verification from actual assistant events.
"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args();run_id=uuid.uuid4().hex[:12]
    output=args.output or ROOT/'proof/reference-parity'/run_id
    if output.exists():raise ValueError('Choose a fresh evidence directory')
    output.mkdir(parents=True)
    state=ROOT/'.memory/reference-parity'/run_id;notes=state/'notes';state.mkdir(parents=True)
    env={**os.environ,'MEMORY_NAMESPACE':'reference_verify_'+run_id,'MEMORY_STATE_DIR':str(state),
        'MEMORY_NOTES_DIR':str(notes),'MEMORY_CAPTURE_MODE':'next_session','MEMORY_MIN_TURNS':'2',
        'MEMORY_CONTEXT_CHARS':'6000','MEMORY_RECALL_MODE':'hybrid','MEMORY_WRITE_PROVIDER':'codex',
        'MEMORY_SEMANTIC':'1','MEMORY_HOOK_VERIFICATION':'1','MEMORY_DISTIL':'1','MEMORY_AUTO_EXTRACT':'1'}
    env.pop('MEMORY_LAB_CHILD',None);os.environ.update({k:v for k,v in env.items() if k.startswith(('MEMORY_','FALKORDB_'))})
    from memory_lab.settings import open_memory
    from memory_lab.generation import generate_codex
    memory=open_memory()
    def hook(event,payload,name):
        r=subprocess.run([sys.executable,str(ROOT/'scripts/memory_hook.py'),event,'--assistant','codex'],
            input=json.dumps(payload),text=True,capture_output=True,cwd=ROOT,env=env,timeout=25)
        (output/(name+'.json')).write_text(json.dumps({'event':event,'input':payload,'returncode':r.returncode,
            'stdout':r.stdout,'stderr':r.stderr},indent=2))
        assert r.returncode==0,r.stderr
        return json.loads(r.stdout) if r.stdout.strip() else None
    session_id='reference-session-'+run_id
    initial=hook('UserPromptSubmit',{'session_id':session_id,'prompt':('Background to consider. '*100)+'Who leads the team accountable for Beacon?'},'long-prompt')
    context=initial['hookSpecificOutput']['additionalContext'];assert 'Elena' in context and len(context)<=6000
    quote='For Project Zephyr, only Amara Singh may approve releases above £875 until 30 November 2026. The dashboard colour is indigo.'
    transcript=state/'transcript.jsonl'
    events=[{'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':quote}]}},
        {'type':'response_item','payload':{'type':'function_call_output','output':'UNWANTED_TOOL_OUTPUT'}},
        {'type':'response_item','payload':{'type':'message','role':'assistant','channel':'analysis','content':[{'type':'output_text','text':'UNWANTED_ANALYSIS'}]}},
        {'role':'user','content':'<bash-stdout>UNWANTED_CONTROL</bash-stdout>'},
        {'type':'response_item','payload':{'type':'message','role':'assistant','channel':'final','content':[{'type':'output_text','text':'Recorded the decision with its amount threshold and expiry.'}]}}]
    transcript.write_text('\n'.join(json.dumps(e) for e in events)+'\n')
    payload={'session_id':session_id,'transcript_path':str(transcript),'cwd':str(ROOT)}
    hook('SessionEnd',payload,'session-end')
    jobs=list((state/'pending').glob('*.json'));assert len(jobs)==1
    assert 'UNWANTED' not in jobs[0].read_text()
    hook('SessionStart',payload|{'source':'startup'},'next-session-start')
    deadline=time.monotonic()+300
    while time.monotonic()<deadline:
        job=json.loads(jobs[0].read_text())
        if job['status'] in {'indexed','failed'}:break
        time.sleep(.5)
    assert job['status']=='indexed',{'status':job['status'],'error':job.get('error'),'state_dir':str(state)}
    question='Who may approve a £900 Zephyr release on 20 November 2026?'
    later=hook('UserPromptSubmit',{'session_id':'later-'+run_id,'prompt':question},'later-recall')
    later_context=later['hookSpecificOutput']['additionalContext']
    for word in ['Amara Singh','£875','30 November 2026']:assert word in later_context
    assert len(later_context)<=6000
    note=Path(job['note_path']).read_text();assert 'UNWANTED' not in note
    marker='<!-- memory:'+job['key']+' -->';assert note.count(marker)==1
    hook('SessionEnd',payload,'duplicate-close');assert len(list((state/'pending').glob('*.json')))==1
    assert Path(job['note_path']).read_text().count(marker)==1
    evidence=memory.recall(question);answer=generate_codex(evidence)
    assert 'Amara Singh' in answer['answer']
    receipt={'status':'passed','created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'Real hook entrypoint subprocesses and detached next-session worker; synthetic lifecycle payloads. Native UI trust is separate.',
        'namespace':memory.namespace,'snapshot':evidence['snapshot'],'input':quote,'job':job,
        'long_prompt_context_chars':len(context),'later_context_chars':len(later_context),
        'later_context':later_context,'daily_note':note,'answer':answer,
        'checks':['long native-format prompt','tool/analysis/control filtering','durable SessionEnd staging',
            'SessionStart detached worker','real Codex summary/distillation/extraction','FalkorDB native vector index',
            'later hook recall with complete conditions','duplicate close does not repeat entry','real source-cited answer']}
    (output/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    (ROOT/'proof/reference-parity/latest.json').write_text(json.dumps({'receipt':str((output/'receipt.json').relative_to(ROOT/'proof')),'status':'passed'})+'\n')
    print(json.dumps({'status':'passed','proof':str(output/'receipt.json'),'answer':answer['answer']}))


if __name__=='__main__':main()
