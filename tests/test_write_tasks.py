from dataclasses import replace
import json

import pytest
from pydantic import ValidationError

from memory_lab.model_tasks import distil_document,extract_document
from memory_lab.install_hooks import install
from memory_lab.sessions import filter_turns,create_session,append_exchange,close_session
from memory_lab.settings import settings
from test_hybrid_memory import memory


def test_distillation_rejects_invented_support():
    doc={'id':'test','title':'Threshold','text':'Approval applies above £750 only until October.','entities':[],'facts':[]}
    def caller(*a):return {'entries':[{'question':'Who approves?','summary':'Unconditional approval','quote':'Always approved.'}]},{}
    with pytest.raises(ValidationError):distil_document(doc,caller)


def test_extraction_reuses_canonical_identity_and_carries_condition():
    quote='Priya Raman approves the Lumen policy only above £750 until 31 October 2026.'
    doc={'id':'test','title':'Lumen','text':quote,'entities':[],'facts':[]}
    known=[{'id':'priya','kind':'Person','name':'Priya Raman','aliases':['Priya']}]
    def caller(prompt,schema):
        assert 'allowed endpoint kinds' in prompt
        return {'entities':[{'name':'Priya','kind':'Person','aliases':[],'description':quote},
            {'name':'Lumen policy','kind':'Policy','aliases':[],'description':quote}],
            'facts':[{'subject':'Lumen policy','predicate':'APPROVED_BY','object':'Priya','quote':quote}]},{}
    extracted,_=extract_document(doc,known,caller)
    assert extracted.entities[0].name=='Priya Raman' and extracted.entities[0].id=='priya'
    assert extracted.facts[0].quote==quote


def test_native_transcript_filter_excludes_private_analysis():
    events=[{'type':'response_item','payload':{'type':'message','role':'assistant','channel':'analysis','content':[{'type':'output_text','text':'PRIVATE'}]}},
        {'type':'response_item','payload':{'type':'message','role':'assistant','channel':'final','content':[{'type':'output_text','text':'Public answer.'}]}}]
    assert filter_turns(events)==[{'role':'assistant','content':'Public answer.'}]


def test_hook_installer_preserves_other_hooks_and_is_idempotent(tmp_path):
    p=tmp_path/'.codex'/'hooks.json';p.parent.mkdir();p.write_text(json.dumps({'hooks':{'Stop':[{'hooks':[{'type':'command','command':'other-hook'}]}]}}))
    install(tmp_path);first=p.read_text();install(tmp_path)
    assert p.read_text()==first
    assert json.loads(first)['hooks']['Stop'][0]['hooks'][0]['command']=='other-hook'


def test_closed_session_rejects_entire_new_exchange(tmp_path):
    cfg=replace(settings(),state_dir=tmp_path/'state',notes_dir=tmp_path/'notes')
    id=create_session(cfg);append_exchange(id,'First','Answer',cfg);close_session(id,cfg,spawn=False)
    with pytest.raises(ValueError):append_exchange(id,'Second','Answer 2',cfg)
    assert len(json.loads((cfg.state_dir/'sessions'/f'{id}.json').read_text())['turns'])==2


def test_process_and_document_links_validate_and_recall(memory):
    from memory_lab.domain import Document
    quote='Release review is part of deployment and references the release guide.'
    doc=Document.model_validate({'id':'release-process','title':'Release review','text':quote,
        'entities':[{'id':'review','name':'Release review','kind':'Process'},
            {'id':'deploy','name':'deployment','kind':'Process'},
            {'id':'guide','name':'release guide','kind':'Document'}],
        'facts':[{'subject':'review','predicate':'PART_OF','object':'deploy','quote':quote},
            {'subject':'review','predicate':'REFERENCES','object':'guide','quote':quote}]})
    memory.replace_documents(memory.documents()['documents']+[doc.model_dump()])
    result=memory.recall('What does Release review reference?')
    assert {'PART_OF','REFERENCES'} <= {e['predicate'] for p in result['paths'] for e in p['edges']}


def test_native_prompt_hook_emits_memory_context(memory,monkeypatch):
    import memory_lab.hook_cli as hooks
    monkeypatch.delenv('MEMORY_LAB_CHILD',raising=False)
    monkeypatch.setattr(hooks,'open_memory',lambda:memory)
    context=hooks.handle('UserPromptSubmit',{'prompt':'Who leads the team accountable for Beacon?'})['hookSpecificOutput']
    assert context['hookEventName']=='UserPromptSubmit'
    assert 'Elena' in context['additionalContext']
    assert 'untrusted source data' in context['additionalContext']
