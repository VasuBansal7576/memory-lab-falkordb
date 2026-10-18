from dataclasses import replace
import json

from memory_lab.sessions import filter_turns,stage_session,process_pending,create_session,append_turn,close_session
from memory_lab.settings import settings
from test_hybrid_memory import memory


def test_filter_keeps_conversation_and_drops_tools_thinking_and_injections():
    events=[{'type':'user','message':{'role':'user','content':[{'type':'text','text':'Use cobalt.'}]}},
        {'type':'assistant','message':{'role':'assistant','content':[{'type':'thinking','text':'SECRET THINKING'}, {'type':'tool_use','name':'shell','input':'SECRET COMMAND'}]}},
        {'type':'response_item','payload':{'type':'message','role':'assistant','content':[{'type':'output_text','text':'Cobalt is selected.'}]}},
        {'type':'response_item','payload':{'type':'function_call_output','output':'SECRET TOOL RESULT'}},
        {'role':'user','content':'<task-notification>SECRET INJECTION'}]
    turns=filter_turns(events)
    assert turns==[{'role':'user','content':'Use cobalt.'},{'role':'assistant','content':'Cobalt is selected.'}]


def test_session_to_daily_note_to_recall_is_idempotent_and_retryable(memory,tmp_path,monkeypatch):
    cfg=replace(settings(),state_dir=tmp_path/'state',notes_dir=tmp_path/'notes')
    id=create_session(cfg);quote='Use cobalt for the release dashboard.'
    append_turn(id,'user',quote,cfg);append_turn(id,'assistant','I recorded that decision.',cfg)
    staged=close_session(id,cfg,spawn=False)
    calls=[]
    def caller(prompt,schema):
        calls.append(schema)
        if 'summary' in schema['properties']:
            return {'summary':quote,'quotes':[quote]},{'input_tokens':10,'output_tokens':5}
        if 'entries' in schema['properties']:
            return {'entries':[{'question':'Which colour is selected for the release dashboard?','summary':quote,'quote':quote}]},{'input_tokens':10,'output_tokens':5}
        return {'entities':[],'facts':[]},{'input_tokens':10,'output_tokens':5}
    import memory_lab.sessions as sessions
    real_sync=sessions.sync_notes
    monkeypatch.setattr(sessions,'sync_notes',lambda *a,**kw: (_ for _ in ()).throw(RuntimeError('temporary index failure')))
    assert process_pending(memory,cfg,caller)[0]['status']=='failed'
    monkeypatch.setattr(sessions,'sync_notes',real_sync)
    assert process_pending(memory,cfg,caller)[0]['status']=='indexed'
    assert process_pending(memory,cfg,caller)==[]
    assert len(calls)==3
    note=next(cfg.notes_dir.glob('*.md')).read_text()
    assert note.count('<!-- memory:')==1
    assert memory.recall('What colour is the cobalt release dashboard?')['citations']
    assert json.loads((cfg.state_dir/'pending'/f"{staged['key']}.json").read_text())['status']=='indexed'


def test_failed_model_preserves_pending_input(memory,tmp_path):
    cfg=replace(settings(),state_dir=tmp_path/'state',notes_dir=tmp_path/'notes')
    staged=stage_session('demo',[{'role':'user','content':'Remember the threshold is 750.'},{'role':'assistant','content':'Recorded.'}],cfg)
    def fail(*args):raise RuntimeError('provider offline')
    assert process_pending(memory,cfg,fail)[0]['status']=='failed'
    assert 'threshold is 750' in (cfg.state_dir/'pending'/f"{staged['key']}.json").read_text()
    assert not list(cfg.notes_dir.glob('*.md'))
