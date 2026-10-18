import json
from pathlib import Path
import subprocess

import pytest

from memory_lab.install_hooks import install
from memory_lab.claude_tasks import call_claude_json


def test_claude_hook_install_preserves_permissions_and_is_idempotent(tmp_path):
    path=tmp_path/'.claude/settings.json';path.parent.mkdir();path.write_text(json.dumps({'model':'sonnet','permissions':{'deny':['Bash(rm *)']},'hooks':{'Stop':[{'hooks':[{'type':'command','command':'existing'}]}]}}))
    install(tmp_path,'claude');first=path.read_text();install(tmp_path,'claude')
    assert path.read_text()==first
    value=json.loads(first)
    assert value['model']=='sonnet' and value['permissions']=={'deny':['Bash(rm *)']}
    assert value['hooks']['Stop'][0]['hooks'][0]['command']=='existing'
    assert all(event in value['hooks'] for event in ['UserPromptSubmit','SessionStart','PreCompact','SessionEnd'])


def test_claude_adapter_uses_structured_output_and_no_tools(monkeypatch):
    def run(command,**kwargs):
        assert command[command.index('--tools')+1]==''
        assert json.loads(command[command.index('--settings')+1])['disableAllHooks'] is True
        assert '--strict-mcp-config' in command and '--no-session-persistence' in command
        assert kwargs['env']['MEMORY_LAB_CHILD']=='1'
        return subprocess.CompletedProcess(command,0,json.dumps({'is_error':False,'structured_output':{'entries':[]},'usage':{'input_tokens':15,'output_tokens':5}}),'')
    monkeypatch.setattr('memory_lab.claude_tasks.subprocess.run',run)
    value,usage=call_claude_json('Source text',{'type':'object'},'sonnet')
    assert value=={'entries':[]} and usage['input_tokens']==15


def test_claude_failures_remain_retryable(monkeypatch):
    monkeypatch.setattr('memory_lab.claude_tasks.subprocess.run',lambda *a,**k:subprocess.CompletedProcess(a,1,'','private provider failure'))
    with pytest.raises(RuntimeError,match='auth status'):call_claude_json('Source',{},'sonnet')
