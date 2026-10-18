"""Optional Claude Code subscription adapter for grounded, tool-free write tasks."""
import json
import os
from pathlib import Path
import subprocess
import tempfile


def call_claude_json(prompt,schema,model):
    with tempfile.TemporaryDirectory(prefix='memory-claude-') as temp:
        command=['claude','-p','--output-format','json','--json-schema',json.dumps(schema),
            '--model',model,'--tools','','--permission-mode','dontAsk','--no-session-persistence',
            '--setting-sources','','--settings',json.dumps({'disableAllHooks':True}),
            '--strict-mcp-config','--mcp-config',json.dumps({'mcpServers':{}})]
        env={**os.environ,'MEMORY_LAB_CHILD':'1','CLAUDE_CODE_DISABLE_AUTO_MEMORY':'1'}
        try:r=subprocess.run(command,input=prompt,text=True,capture_output=True,cwd=temp,env=env,timeout=120)
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise RuntimeError('Claude write task unavailable; retained input can be retried') from exc
    if r.returncode!=0:raise RuntimeError('Claude write task failed; check claude auth status')
    try:
        packet=json.loads(r.stdout)
        if packet.get('is_error'):raise ValueError('Provider reported failure')
        value=packet.get('structured_output')
        if value is None:value=json.loads(packet['result'])
        if not isinstance(value,dict):raise ValueError('Structured result must be an object')
        usage=packet['usage']
        for key in ['input_tokens','output_tokens']:
            if type(usage.get(key)) is not int or usage[key]<0:raise ValueError('Provider usage unavailable')
        return value,{**usage,'provider':'claude','model':model}
    except (ValueError,KeyError,TypeError) as exc:
        raise RuntimeError('Claude returned invalid structured output or usage') from exc
