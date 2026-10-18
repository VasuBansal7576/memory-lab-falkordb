"""Verify trusted Codex sessions actually capture and recall across process exits."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]


def main():
    id=uuid.uuid4().hex[:12];state=ROOT/'.memory/native-lifecycle'/id;state.mkdir(parents=True)
    env={**os.environ,'MEMORY_NAMESPACE':'native_verify_'+id,'MEMORY_STATE_DIR':str(state),
        'MEMORY_NOTES_DIR':str(state/'notes'),'MEMORY_CAPTURE_MODE':'headless','MEMORY_MIN_TURNS':'2',
        'MEMORY_WRITE_PROVIDER':'codex','MEMORY_DISTIL':'1','MEMORY_AUTO_EXTRACT':'1','MEMORY_RECALL_MODE':'hybrid'}
    env.pop('MEMORY_LAB_CHILD',None);env.pop('MEMORY_HOOK_VERIFICATION',None)
    command=['codex','exec','--ignore-rules','--json','--skip-git-repo-check','--sandbox','read-only',
        '-m','gpt-6.1-sol','-c','model_reasoning_effort="low"','-c','approval_policy="never"',
        '-c','suppress_unstable_features_warning=true','--disable','plugins','--disable','apps','--disable','skill_search',
        '--disable','multi_agent','--enable','skip_host_skill_discovery','-C',str(ROOT),'-']
    source='For Project Sylvan, only Noor Desai may approve releases above £640 until 15 December 2026.'
    first=subprocess.run(command,input=source+' Remember this decision for later sessions. Do not call tools. Reply only Recorded.',
        capture_output=True,text=True,env=env,cwd=ROOT,timeout=90)
    assert first.returncode==0,'First native session failed'
    deadline=time.monotonic()+300;job=None
    while time.monotonic()<deadline:
        pending=list((state/'pending').glob('*.json'))
        if pending:
            job=json.loads(pending[0].read_text())
            if job['status'] in {'indexed','failed'}:break
        time.sleep(.5)
    assert job and job['status']=='indexed',{'state':str(state),'job_status':job.get('status') if job else 'no captured job','error':job.get('error') if job else None}
    second=subprocess.run(command,input='Do not call tools. Using workspace memory, who may approve a £900 Sylvan release on 20 November 2026? Give the approver and the applicable threshold and expiry with source markers.',
        capture_output=True,text=True,env=env|{'MEMORY_CAPTURE_MODE':'off'},cwd=ROOT,timeout=90)
    assert second.returncode==0,'Later native session failed'
    events=[json.loads(line) for line in second.stdout.splitlines() if line.strip()]
    messages=[e['item']['text'] for e in events if e.get('type')=='item.completed' and e.get('item',{}).get('type')=='agent_message']
    answer=messages[-1]
    for value in ['Noor Desai','640','15 December 2026']:assert value in answer,answer
    hooks=[json.loads(line) for line in (state/'hook-receipts.jsonl').read_text().splitlines()]
    assert len([e for e in hooks if e['event']=='SessionStart'])>=2
    assert len([e for e in hooks if e['event']=='SessionEnd'])>=2
    assert len([e for e in hooks if e['event']=='UserPromptSubmit' and e['context_chars']>0])>=2
    assert all(not e['verification'] for e in hooks)
    receipt={'status':'passed','created_at':datetime.now(timezone.utc).isoformat(),
        'scope':'Actual trusted Codex exec sessions, actual SessionEnd transcript capture, detached worker, and a separate later Codex process.',
        'namespace':env['MEMORY_NAMESPACE'],'source':source,'hooks':hooks,'job':job,'answer':answer,
        'first_native_trace':first.stdout,'later_native_trace':second.stdout}
    output=ROOT/'proof/reference-parity/native-lifecycle.json';output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':'passed','receipt':str(output),'answer':answer}))


if __name__=='__main__':main()
