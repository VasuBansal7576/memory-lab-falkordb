import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.compare_graph import (SCHEMA, FEATURE_ARGS, build_prompt, grade_response,
                                   parse_trace, run_model, summarize, validate_response)

FIXTURE = json.loads(Path('data/operations.json').read_text())
GOLD = FIXTURE['questions']['october-refund']
DECISION = {'verdict': 'supported', 'entities': ['Mira Shah'], 'source_ids': GOLD['sources']}


def trace(decision=None, commands=()):
    events = [{'type': 'turn.started'}]
    for command in commands:
        events.append({'type': 'item.completed', 'item': {'type': 'command_execution',
                       'command': command, 'aggregated_output': 'source text', 'exit_code': 0}})
    events.extend([{'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps(decision or DECISION)}},
                   {'type': 'turn.completed', 'usage': {'input_tokens': 100, 'cached_input_tokens': 60, 'output_tokens': 20}}])
    return '\n'.join(json.dumps(e) for e in events)


def test_contradictory_prose_is_not_accepted_as_a_scored_decision():
    bad = {**DECISION, 'answer': 'Mira Shah does not approve this. Noah Chen does.'}
    with pytest.raises(ValueError, match='prose'):
        grade_response(GOLD, bad, FIXTURE['documents'])
    assert 'answer' not in SCHEMA['properties']


def test_decision_requires_correct_identity_and_all_hop_sources():
    assert grade_response(GOLD, DECISION, FIXTURE['documents'])['decision_correct']
    for changed in [{'entities': ['Noah Chen']}, {'source_ids': ['october-cover']},
                    {'verdict': 'missing'}, {'source_ids': GOLD['sources'] + ['invented']}]:
        assert not grade_response(GOLD, {**DECISION, **changed}, FIXTURE['documents'])['decision_correct']


def test_unique_aliases_and_ids_resolve_to_same_identity():
    for label in ['Mira', 'Mira Shah', 'mira']:
        assert grade_response(GOLD, {**DECISION, 'entities': [label]}, FIXTURE['documents'])['decision_correct']
    q = FIXTURE['questions']['ambiguous']
    valid = {'verdict': 'ambiguous', 'entities': ['alex-design', 'Alex Rivera'], 'source_ids': []}
    assert grade_response(q, valid, FIXTURE['documents'])['decision_correct']
    assert not grade_response(q, {**valid, 'entities': ['Alex']}, FIXTURE['documents'])['decision_correct']
    assert not grade_response(q, {**valid, 'entities': []}, FIXTURE['documents'])['decision_correct']
    with pytest.raises(ValueError, match='Duplicate'):
        validate_response({**valid, 'entities': ['Alex', 'Alex']})


def test_search_arm_must_perform_real_search_and_read():
    with pytest.raises(ValueError, match='actually search'):
        parse_trace(trace(), 'search')
    decision, usage, commands = parse_trace(trace(commands=['rg -n refund corpus', 'cat corpus/customer-handbook.txt']), 'search')
    assert decision == DECISION and len(commands) == 2
    assert usage['input_tokens'] == 100 and usage['cached_input_tokens'] == 60


def test_graph_arm_rejects_tools_and_uses_actual_usage():
    assert parse_trace(trace(), 'graph')[1]['input_tokens'] == 100
    with pytest.raises(ValueError, match='Graph arm'):
        parse_trace(trace(commands=['cat corpus/customer-handbook.txt']), 'graph')


def test_both_arms_use_identical_tool_configuration_and_directory(monkeypatch, tmp_path):
    schema = tmp_path/'schema.json'
    schema.write_text(json.dumps(SCHEMA))
    calls = []
    def fake(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=trace(), stderr='', returncode=0)
    monkeypatch.setattr('scripts.compare_graph.subprocess.run', fake)
    for arm in ['search', 'graph']:
        run_model(build_prompt('same question', arm, 'recalled facts'), schema, 'same-model', 60, tmp_path)
    assert calls[0] == calls[1]
    assert '--ignore-user-config' in calls[0]
    assert 'plugins' in FEATURE_ARGS and 'apps' in FEATURE_ARGS


def test_failed_rows_remain_in_denominator_and_outside_latency_stats():
    rows = []
    for arm in ['search','graph']:
        for valid in [True,False]:
            rows.append(dict(arm=arm,question_id='october-refund',valid=valid,decision_correct=valid,
                total_s=10 if valid else .01,recall_s=0,input_tokens=100,cached_input_tokens=60,
                output_tokens=20,tool_calls=2,tool_output_bytes=30,prompt_utf8_bytes=100))
    s = summarize(rows)
    assert s['failures'] == 2
    for a in s['arms'].values():
        assert a['runs'] == 2 and a['decisions_correct'] == 1
        assert a['approval']['total_s']['median'] == 10


def test_current_graph_benchmark_does_not_receive_note_search_results(monkeypatch,tmp_path):
    import scripts.compare_graph as comparison
    from memory_lab.memory import Memory
    import uuid
    m=Memory(namespace='benchmark_test_'+uuid.uuid4().hex[:12])
    monkeypatch.setattr(comparison,'Memory',lambda **kwargs:m)
    monkeypatch.setattr(comparison,'run_model',lambda *args,**kwargs:{'stdout':trace(commands=['rg refund corpus','cat corpus/customer-handbook.txt']) if 'Evidence is in corpus' in args[0] else trace(),
        'stderr':'','returncode':0,'elapsed_s':.1})
    args=SimpleNamespace(output=str(tmp_path/'proof'),fixture=str(Path('data/operations.json').resolve()),
        question_ids='october-refund',port=6387,model='test-model',repeats=1,timeout=10)
    try:
        assert comparison.run_suite(args)==0
        recalled=json.loads((tmp_path/'proof/raw/october-refund-1-graph.recall.json').read_text())
        assert 'search_legs' not in recalled['trace']
        assert {c['document_id'] for c in recalled['citations']}=={'customer-handbook','staffing-register','october-cover'}
    finally:
        for name in m.db.list_graphs():
            if name.startswith(m.namespace+'__'):m.db.select_graph(name).delete()
        for key in m.redis.scan_iter(match=m.namespace+':*'):m.redis.delete(key)


def test_provider_accounting_includes_invalid_attempts_and_marks_unknown_usage(tmp_path):
    from scripts.compare_models import provider_accounting
    rows=[]
    for id,valid,content in [('good',True,trace()),('invalid',False,trace()),('timeout',False,'')]:
        (tmp_path/(id+'.jsonl')).write_text(content)
        rows.append({'model':'test-model','run_id':id,'valid':valid,'trace_path':id+'.jsonl'})
    result=provider_accounting(tmp_path,rows)
    assert result['attempts']==3 and result['completed_provider_turns']==2
    assert result['reported_token_totals']=={'input_tokens':200,'cached_input_tokens':120,'output_tokens':40}
    assert result['usage_unavailable_for']==['test-model/timeout']
