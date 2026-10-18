"""Behavioral regressions found by comparing the pinned reference implementation."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from memory_lab.context import compact_evidence
from memory_lab.notes import sync_notes,note_files
from memory_lab.sessions import filter_turns,stage_session,process_pending
from memory_lab.settings import settings
from test_hybrid_memory import memory


def test_long_native_prompt_retains_recall(memory,monkeypatch):
    import memory_lab.hook_cli as hook
    monkeypatch.setattr(hook,'open_memory',lambda:memory)
    prompt=('Please consider the background carefully. '*100)+'Who leads the team accountable for Beacon?'
    result=hook.handle('UserPromptSubmit',{'prompt':prompt})
    assert 'Elena' in result['hookSpecificOutput']['additionalContext']


def test_context_budget_omits_whole_condition_instead_of_cutting_it():
    quote='Only Priya approves above £750 until 31 October. '+('Do not omit this condition. '*30)
    evidence={'citations':[{'id':'S1','document_id':'rules','quote':quote}], 'paths':[]}
    result=compact_evidence(evidence,max_chars=220)
    assert len(result)<=220
    assert 'incomplete' in result.lower()
    assert 'Only Priya' not in result


def test_one_failed_model_note_keeps_raw_sources_and_other_processed_notes(memory,tmp_path):
    (tmp_path/'bad.md').write_text('# Unprocessable\nThe cobalt spaceship leaves tomorrow.\n')
    (tmp_path/'good.md').write_text('# Train expense\nTrain fares need a receipt.\n')
    def caller(prompt,schema):
        if 'cobalt spaceship' in prompt:raise RuntimeError('model unavailable for this note')
        return {'entries':[{'question':'What do train fares need?','summary':'Receipts required.','quote':'Train fares need a receipt.'}]},{}
    result=sync_notes(memory,tmp_path,distil=True,caller=caller)
    assert result['errors'] and result['updated']==2
    assert any('cobalt spaceship' in h['quote'] for h in memory.search('cobalt spaceship')['hits'])
    assert any(d['distilled'] for d in memory.documents()['documents'] if d['title']=='Train expense')


def test_invalid_yaml_does_not_block_raw_notes(memory,tmp_path):
    (tmp_path/'bad.md').write_text('---\ntitle: [broken\n---\nThe vermilion dashboard remains online.\n')
    (tmp_path/'good.md').write_text('# Normal note\nA normal searchable source.\n')
    result=sync_notes(memory,tmp_path)
    assert result['updated']==2 and result['errors']
    assert any('vermilion dashboard' in h['quote'] for h in memory.search('vermilion dashboard')['hits'])


def test_project_inside_notes_is_not_indexed(tmp_path,monkeypatch):
    import memory_lab.notes as notes
    project=tmp_path/'implementation';project.mkdir();(project/'README.md').write_text('Internal project docs.')
    (tmp_path/'real.md').write_text('A genuine note.')
    monkeypatch.setattr(notes,'PROJECT_ROOT',project,raising=False)
    assert note_files(tmp_path)==[tmp_path/'real.md']


@pytest.mark.parametrize('control',['<command-name>/clear</command-name>','<command-args>foo</command-args>',
    '<bash-stdout>tool output</bash-stdout>','[Request interrupted by user for tool use]',
    '<session-start-hook>context</session-start-hook>'])
def test_transcript_control_events_are_not_user_memory(control):
    assert filter_turns([{'role':'user','content':control}])==[]


def test_digest_limits_are_configurable(tmp_path,monkeypatch):
    monkeypatch.setenv('MEMORY_MAX_TURNS','2');monkeypatch.setenv('MEMORY_MAX_CHARS','100')
    cfg=replace(settings(),state_dir=tmp_path/'state',notes_dir=tmp_path/'notes')
    staged=stage_session('settings-check',[{'role':'user','content':'old turn'},{'role':'assistant','content':'middle turn'},
        {'role':'user','content':'new turn'}],cfg)
    turns=json.loads(Path(staged['path']).read_text())['turns']
    assert [t['content'] for t in turns]==['middle turn','new turn']


def test_neighboring_lines_include_condition_after_chunk_boundary(memory,tmp_path):
    text='# Launch policy\n'+('padding '*140+'\n')+'The obsidian release is approved.\n# Exception\nApproval is suspended after 31 October 2026.\n'
    (tmp_path/'launch.md').write_text(text)
    sync_notes(memory,tmp_path)
    result=next(h for h in memory.search('obsidian release',top_k=10)['hits'] if 'obsidian' in h['quote'])
    assert 'Approval is suspended after 31 October 2026.' in result['quote']
    assert text[result['start']:result['end']]==result['quote']


def test_frontmatter_links_are_explicit_source_backed_document_edges(memory,tmp_path):
    (tmp_path/'onboarding.md').write_text('---\ntype: process\ntitle: Onboarding\nentities: [Onboarding, Ops Manager]\nlinks: [guide]\n---\nFollow the checklist.\n')
    (tmp_path/'guide.md').write_text('# Day-one guide\nCreate the initial accounts.\n')
    sync_notes(memory,tmp_path)
    result=memory.recall('What does Onboarding reference?')
    assert any(e['predicate']=='REFERENCES' for p in result['paths'] for e in p['edges'])
    assert any('guide' in c['quote'] for c in result['citations'])


def test_reference_nodes_edges_aliases_are_imported_with_real_source_quotes(memory,tmp_path):
    extracts=tmp_path/'extractions';extracts.mkdir()
    body='Refund approvals require Ops Manager approval above £500 until November.\n'
    (tmp_path/'policy.md').write_text('---\ntype: policy\ntitle: Refund approvals\n---\n'+body)
    payload={'source_doc':'policy.md','nodes':[{'name':'Refund approvals','type':'POLICY','description':'An unsafe paraphrase'},
        {'name':'Ops Manager','type':'ROLE','description':'Ops Manager'}],
        'edges':[{'source':'Refund approvals','predicate':'approved_by','target':'Ops Manager'}],
        'aliases':[{'entity':'Refund approvals','alias':'reimbursements'}]}
    (extracts/'policy.json').write_text(json.dumps(payload))
    result=sync_notes(memory,tmp_path,extractions=extracts)
    assert not result['errors']
    recalled=memory.recall('Who approves reimbursements?')
    assert any(e['predicate']=='APPROVED_BY' for p in recalled['paths'] for e in p['edges'])
    assert body.strip() in recalled['evidence_text']
    assert 'An unsafe paraphrase' not in recalled['evidence_text']


def test_completed_model_work_survives_publication_failure(memory,tmp_path,monkeypatch):
    from memory_lab.memory import MemoryUnavailable
    (tmp_path/'note.md').write_text('# Iris\nThe iris service uses cobalt.\n')
    calls=[]
    def caller(prompt,schema):
        calls.append(schema)
        if 'entries' in schema['properties']:return {'entries':[{'question':'What colour?','summary':'Cobalt','quote':'The iris service uses cobalt.'}]},{'input_tokens':10,'output_tokens':2}
        return {'entities':[],'facts':[]},{'input_tokens':12,'output_tokens':3}
    publish=memory.replace_documents
    monkeypatch.setattr(memory,'replace_documents',lambda *a:(_ for _ in ()).throw(MemoryUnavailable('temporary outage')))
    with pytest.raises(MemoryUnavailable):sync_notes(memory,tmp_path,distil=True,extract=True,caller=caller)
    monkeypatch.setattr(memory,'replace_documents',publish)
    result=sync_notes(memory,tmp_path,distil=True,extract=True,caller=caller)
    assert len(calls)==2
    assert not result['errors']
    assert memory.search('iris cobalt')['hits']


def test_disabled_distillation_and_custom_voice_and_log_folder(memory,tmp_path):
    voice=tmp_path/'voice.txt';voice.write_text('CUSTOM VOICE: only decisions.')
    cfg=replace(settings(),state_dir=tmp_path/'state',notes_dir=tmp_path/'notes',digest_prompt=voice,
        distil=False,auto_extract=False,log_dir='daily')
    quote='Use saffron for the release dashboard.'
    staged=stage_session('custom',[{'role':'user','content':quote},{'role':'assistant','content':'Recorded.'}],cfg)
    def caller(prompt,schema):
        assert 'CUSTOM VOICE' in prompt
        assert set(schema['properties'])=={'summary','quotes'}
        return {'summary':quote,'quotes':[quote]},{}
    assert process_pending(memory,cfg,caller)[0]['status']=='indexed'
    assert len(list((cfg.notes_dir/'daily').glob('*.md')))==1
    assert json.loads(Path(staged['path']).read_text())['index_usage']==[]


def test_processing_limit_does_not_hide_unprocessed_raw_sources(memory,tmp_path):
    for n in ['one','two']:(tmp_path/(n+'.md')).write_text('# '+n+'\nA tangerine release decision.\n')
    calls=[]
    def caller(prompt,schema):calls.append(prompt);return {'entries':[]},{}
    result=sync_notes(memory,tmp_path,distil=True,caller=caller,limit=1)
    assert len(calls)==1 and len(result['deferred'])==1
    assert len([d for d in memory.documents()['documents'] if 'tangerine' in d['text']])==2


def test_reminder_before_real_user_text_is_removed_without_losing_the_message():
    assert filter_turns([{'role':'user','content':'<system-reminder>harness</system-reminder>Choose cobalt.'},
        {'type':'response_item','payload':None}])==[{'role':'user','content':'Choose cobalt.'}]


def test_renamed_link_target_updates_without_stale_entity_conflict(memory,tmp_path):
    (tmp_path/'source.md').write_text('---\ntitle: Iris process\nlinks: [target]\n---\nUse the guide.\n')
    target=tmp_path/'target.md';target.write_text('# Original guide\nA source.\n')
    sync_notes(memory,tmp_path)
    target.write_text('# Renamed guide\nA source.\n')
    sync_notes(memory,tmp_path)
    result=memory.recall('What does Iris process reference?')
    assert any(n['name']=='Renamed guide' for p in result['paths'] for n in p['nodes'])


def test_embedding_setup_failure_can_recover_without_source_edits(memory,monkeypatch):
    import memory_lab.retrieval as retrieval
    monkeypatch.setenv('MEMORY_SEMANTIC','1')
    real=retrieval.embed
    monkeypatch.setattr(retrieval,'embed',lambda text:(_ for _ in ()).throw(RuntimeError('download interrupted')))
    memory.ensure_seed(Path('data/operations.json'))
    before=memory._record();assert before['semantic_error']
    monkeypatch.setattr(retrieval,'embed',real)
    memory.ensure_seed(Path('data/operations.json'))
    assert memory.overview()['semantic_model'] and memory.overview()['semantic_error'] is None
    assert memory.overview()['snapshot']!=before['snapshot']
    assert json.loads(memory.redis.get(f"{memory.namespace}:snapshot:{before['snapshot']}"))==before


def test_graph_and_rag_can_be_used_independently(memory):
    rag=memory.recall('What is the onboarding process?',mode='rag')
    assert rag['citations'] and not rag['paths']
    graph=memory.recall('Who leads the team accountable for Beacon?',mode='graph')
    assert graph['paths'] and 'search_legs' not in graph['trace']


def test_next_session_mode_processes_pending_when_app_chat_starts(tmp_path,monkeypatch):
    import memory_lab.sessions as sessions
    cfg=replace(settings(),state_dir=tmp_path/'state',notes_dir=tmp_path/'notes',capture_mode='next_session')
    stage_session('old',[{'role':'user','content':'Keep the saffron decision.'},{'role':'assistant','content':'Recorded.'}],cfg)
    called=[];monkeypatch.setattr(sessions,'spawn_worker',lambda value:called.append(value))
    sessions.create_session(cfg)
    assert called==[cfg]


def test_corrupt_pending_job_does_not_stop_other_memories(memory,tmp_path):
    cfg=replace(settings(),state_dir=tmp_path/'state',notes_dir=tmp_path/'notes',distil=False,auto_extract=False)
    staged=stage_session('valid',[{'role':'user','content':'Choose silver.'},{'role':'assistant','content':'Recorded.'}],cfg)
    (cfg.state_dir/'pending/000-corrupt.json').write_text('{broken')
    def caller(*a):return {'summary':'Choose silver.','quotes':['Choose silver.']},{}
    result=process_pending(memory,cfg,caller)
    assert any(r['status']=='indexed' for r in result)
    assert json.loads(Path(staged['path']).read_text())['status']=='indexed'
    assert (cfg.state_dir/'pending/000-corrupt.json').read_text()=='{broken'


def test_reference_exports_resolve_cross_document_endpoints_without_duplicate_source_entities(memory,tmp_path):
    extracts=tmp_path/'exports';extracts.mkdir()
    (tmp_path/'process.md').write_text('---\ntype: process\ntitle: Starter process\nlinks: [roles]\n---\nThe Starter process is owned by the Duty Lead.\n')
    (tmp_path/'roles.md').write_text('# Duty assignments\nThe Duty Lead role is held by Nila Rao.\n')
    (extracts/'process.json').write_text(json.dumps({'source_doc':'process.md',
        'nodes':[{'type':'PROCESS','name':'Starter process'}],
        'edges':[{'source':'Starter process','predicate':'references','target':'Duty Lead'}], 'aliases':[]}))
    (extracts/'roles.json').write_text(json.dumps({'source_doc':'roles.md',
        'nodes':[{'type':'ROLE','name':'Duty Lead'},{'type':'PERSON','name':'Nila Rao'}],
        'edges':[{'source':'Duty Lead','predicate':'held_by','target':'Nila Rao'}], 'aliases':[]}))
    result=sync_notes(memory,tmp_path,extractions=extracts)
    assert not result['errors']
    evidence=memory.recall('Who owns the Starter process?',mode='graph')
    assert evidence['status']=='recalled'
    assert 'Nila Rao' in evidence['evidence_text']
    assert not sync_notes(memory,tmp_path,extractions=extracts)['changed']


def test_malformed_reference_export_does_not_block_other_raw_notes(memory,tmp_path):
    extracts=tmp_path/'exports';extracts.mkdir()
    (tmp_path/'bad.md').write_text('# Malformed export source\nThe copper policy remains searchable.\n')
    (tmp_path/'good.md').write_text('# Independent source\nThe jade policy remains searchable.\n')
    (extracts/'bad.json').write_text(json.dumps({'source_doc':'bad.md','nodes':None,'edges':[]}))
    result=sync_notes(memory,tmp_path,extractions=extracts)
    assert result['errors'] and result['updated']==2
    assert any('jade policy' in h['quote'] for h in memory.search('jade policy')['hits'])


@pytest.mark.parametrize('source_id',['source-a','source-b','source-c'])
def test_document_links_cannot_crowd_out_three_hop_authority_chain(memory,source_id):
    from memory_lab.domain import Document
    entities=[{'id':'policy','name':'Cobalt refunds','kind':'Policy'},
        {'id':'role','name':'Duty approver','kind':'Role'},
        {'id':'holder','name':'Nila Rao','kind':'Person'},
        {'id':'delegate','name':'Omar Bell','kind':'Person'}]
    facts=[{'subject':'policy','predicate':'APPROVED_BY','object':'role',
            'quote':'Cobalt refunds above £600 require the Duty approver.'},
        {'subject':'role','predicate':'HELD_BY','object':'holder',
            'quote':'Nila Rao holds the Duty approver role.'},
        {'subject':'holder','predicate':'DELEGATES_TO','object':'delegate',
            'quote':'Nila Rao delegates approval to Omar Bell only in November 2026.'}]
    for i in range(10):
        entities.append({'id':f'guide-{i}','name':f'Guide {i}','kind':'Process'})
        facts.append({'subject':f'guide-{i}','predicate':'REFERENCES','object':'policy',
            'quote':f'Guide {i} references Cobalt refunds.'})
    memory.replace_documents([Document(id=source_id,title='Cobalt operations',
        text='\n'.join(f['quote'] for f in facts),entities=entities,facts=facts)])
    result=memory.recall('Who approves Cobalt refunds in November?',mode='graph')
    predicates={e['predicate'] for p in result['paths'] for e in p['edges']}
    assert {'APPROVED_BY','HELD_BY','DELEGATES_TO'}<=predicates
    assert 'Omar Bell only in November 2026.' in result['evidence_text']
    assert len(result['paths'])==8 and result['trace']['truncated']
