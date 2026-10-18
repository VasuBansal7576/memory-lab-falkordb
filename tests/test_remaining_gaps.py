import json
import os
import subprocess
import sys
from pathlib import Path

from memory_lab.context import compact_evidence


def test_source_condition_is_preserved_even_without_a_description():
    quote = 'Noah delegates to Mira only from 1 October through 31 October 2026.'
    evidence = {'paths': [{'nodes': [{'id': 'noah','name':'Noah'}, {'id':'mira','name':'Mira'}],
        'edges':[{'subject':'noah','predicate':'DELEGATES_TO','object':'mira','citation_ids':['S1']}]}],
        'citations':[{'id':'S1','document_id':'cover','quote':quote}]}
    assert quote in compact_evidence(evidence)


def test_optional_hook_does_not_block_a_prompt_when_database_is_down():
    result = subprocess.run([sys.executable,'-m','memory_lab.recall_cli','--hook'],
        input=json.dumps({'prompt':'Who owns Beacon?'}),text=True,capture_output=True,
        env={**os.environ,'FALKORDB_PORT':'1'},timeout=12)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ''
