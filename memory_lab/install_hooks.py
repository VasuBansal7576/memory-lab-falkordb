"""Install project-local Codex or Claude definitions without changing permissions."""
import argparse
import json
from pathlib import Path
import shlex
import sys

from .settings import ROOT
from .state import atomic_json


def install(project=ROOT,assistant='codex'):
    if assistant not in {'codex','claude','both'}:raise ValueError('Unknown assistant')
    if assistant=='both':return {'installed':[install(project,'codex'),install(project,'claude')]}
    project=Path(project).expanduser().resolve()
    path=project/('.codex/hooks.json' if assistant=='codex' else '.claude/settings.json')
    value=json.loads(path.read_text()) if path.exists() else {};groups=value.setdefault('hooks',{})
    for event in ['UserPromptSubmit','SessionStart','PreCompact','SessionEnd']:
        command=shlex.join([sys.executable,str(ROOT/'scripts/memory_hook.py'),event,'--assistant',assistant])
        existing=groups.setdefault(event,[])
        # Refresh our own obsolete command only; preserve every unrelated setting/hook.
        for group in existing:
            group['hooks']=[h for h in group.get('hooks',[]) if not (str(ROOT/'scripts/memory_hook.py') in h.get('command','') and event in h.get('command',''))]
        existing[:]=[g for g in existing if g.get('hooks')]
        existing.append({'hooks':[{'type':'command','command':command,'timeout':15 if event=='UserPromptSubmit' else 3 if assistant=='codex' and event=='SessionEnd' else 5}]})
    atomic_json(path,value)
    return {'path':str(path),'events':list(groups),'assistant':assistant,
        'activation':'Review exact definitions in Codex /hooks; restart Claude Code after reviewing its project settings. No trust bypass was installed.'}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',type=Path,default=ROOT)
    p.add_argument('--assistant',choices=['codex','claude','both'],default='codex')
    args=p.parse_args();print(json.dumps(install(args.project,args.assistant),indent=2))


if __name__=='__main__':main()
