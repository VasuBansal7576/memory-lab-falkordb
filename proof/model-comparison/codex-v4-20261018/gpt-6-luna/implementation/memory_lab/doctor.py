"""Read-only runtime and hook activation checks without printing credentials."""
import json
import shutil
import subprocess

from .settings import ROOT,settings,open_memory


def status():
    cfg=settings();checks={'write_provider':cfg.write_provider,'capture_mode':cfg.capture_mode,'notes_dir':str(cfg.notes_dir),
        'recall_mode':cfg.recall_mode,'context_chars':cfg.context_chars}
    for assistant,command in [('codex',['codex','login','status']),('claude',['claude','auth','status'])]:
        configured=False
        if shutil.which(assistant):
            try:configured=subprocess.run(command,capture_output=True,text=True,timeout=10).returncode==0
            except (OSError,subprocess.TimeoutExpired):pass
        checks[assistant]={'installed':bool(shutil.which(assistant)),'authenticated':configured,
            'hooks_installed':(ROOT/('.codex/hooks.json' if assistant=='codex' else '.claude/settings.json')).exists()}
    receipts=cfg.state_dir/'hook-receipts.jsonl';seen=[]
    if receipts.exists():
        for line in receipts.read_text().splitlines():
            try:seen.append(json.loads(line))
            except ValueError:continue
    checks['native_hook_events']=[r for r in seen if not r.get('verification')][-20:]
    checks['verification_hook_events']=[r for r in seen if r.get('verification')][-20:]
    try:checks['database']={'status':'connected',**open_memory(seed=False).overview()}
    except Exception as exc:checks['database']={'status':'unavailable','error':type(exc).__name__}
    checks['native_hook_activation']='Observed handler events; confirm these were native sessions.' if checks['native_hook_events'] else 'Not verified. Codex requires review in /hooks; Claude requires settings reload.'
    return checks


def main():print(json.dumps(status(),indent=2))


if __name__=='__main__':main()
