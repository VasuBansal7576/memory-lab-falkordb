"""Codex lifecycle hooks; optional memory must never block a conversation."""
import json
import os
from pathlib import Path
import sys

from .settings import open_memory,settings
from .context import hook_context
from .sessions import stage_session,transcript_turns,spawn_worker,has_pending


def handle(event,payload):
    if os.environ.get('MEMORY_LAB_CHILD'):return None
    cfg=settings()
    if event=='UserPromptSubmit':
        prompt=payload.get('prompt','')
        if not isinstance(prompt,str) or not prompt.strip():return None
        evidence=open_memory().recall(prompt)
        if not evidence['citations'] and not evidence['candidates']:return None
        return {'hookSpecificOutput':{'hookEventName':event,
            'additionalContext':hook_context(evidence,cfg)}}
    if event in {'SessionEnd','PreCompact'}:
        transcript=payload.get('transcript_path')
        if not transcript or not Path(transcript).is_file():return None
        job=stage_session(str(payload.get('session_id','session')),transcript_turns(transcript,cfg),cfg)
        if job.get('status') in {'pending','failed'} and cfg.capture_mode=='headless':spawn_worker(cfg)
    if event=='SessionStart' and cfg.capture_mode!='off':
        if has_pending(cfg):spawn_worker(cfg)
    return None


def main():
    import argparse
    from datetime import datetime,timezone
    from hashlib import sha256
    from filelock import FileLock
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('event');parser.add_argument('--assistant',choices=['codex','claude'],default='codex')
    args=parser.parse_args()
    try:
        payload=json.load(sys.stdin)
        result=handle(args.event,payload)
        if result:print(json.dumps(result,ensure_ascii=False))
        cfg=settings();cfg.state_dir.mkdir(parents=True,exist_ok=True)
        receipt={'event':args.event,'assistant':args.assistant,'at':datetime.now(timezone.utc).isoformat(),
            'session_hash':sha256(str(payload.get('session_id','')).encode()).hexdigest()[:16],
            'context_chars':len((result or {}).get('hookSpecificOutput',{}).get('additionalContext','')),
            'verification':os.environ.get('MEMORY_HOOK_VERIFICATION')=='1'}
        path=cfg.state_dir/'hook-receipts.jsonl'
        with FileLock(str(path)+'.lock',timeout=2):
            with path.open('a') as f:f.write(json.dumps(receipt)+'\n')
    except Exception as exc:
        # Record diagnosis without source text, credentials, or exception bodies.
        try:
            cfg=settings();cfg.state_dir.mkdir(parents=True,exist_ok=True)
            with (cfg.state_dir/'hook-errors.log').open('a') as f:f.write(type(exc).__name__+'\n')
        except Exception:pass


if __name__=='__main__':main()
