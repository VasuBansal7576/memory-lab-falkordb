from pathlib import Path

from memory_lab.notes import sync_notes
from memory_lab.retrieval import chunks,fuse
from test_hybrid_memory import memory


def fixture(tmp_path):
    notes=tmp_path/'notes';notes.mkdir();(notes/'daily').mkdir()
    source=notes/'daily'/'approval.md'
    source.write_text('# Duty approval\nNila approves invoices above £600 only in November.\n')
    distilled=notes/'distilled';distilled.mkdir()
    sidecar=distilled/'entry.md'
    sidecar.write_text('source: daily/approval.md\n- Who signs the invoices?\n- What is the threshold?\nsummary: November cover.\nrule: Above £600 only in November.\nquote: "Nila approves invoices above £600 only in November."\n')
    return notes,distilled,source,sidecar


def test_legacy_distilled_markdown_import_is_grounded_and_idempotent(memory,tmp_path):
    notes,distilled,_,_=fixture(tmp_path)
    first=sync_notes(memory,notes,distilled=distilled)
    assert not first['errors'] and first['updated']==1
    document=next(d for d in memory.documents()['documents'] if d['title']=='Duty approval')
    assert len(document['distilled'])==2
    assert document['distilled'][0]['rule']=='Above £600 only in November.'
    assert document['distilled_import_hash'] and 'distilled' in document['processed']
    assert any(h['kind']=='distilled' for h in memory.search('Who signs the invoices?')['hits'])
    assert not sync_notes(memory,notes,distilled=distilled)['changed']


def test_sidecar_edit_and_deletion_invalidate_previously_imported_entries(memory,tmp_path):
    notes,distilled,_,sidecar=fixture(tmp_path)
    sync_notes(memory,notes,distilled=distilled)
    sidecar.write_text(sidecar.read_text().replace('Who signs the invoices?','Who covers invoice approval?'))
    assert sync_notes(memory,notes,distilled=distilled)['changed']
    document=next(d for d in memory.documents()['documents'] if d['title']=='Duty approval')
    assert document['distilled'][0]['question']=='Who covers invoice approval?'
    sidecar.unlink();sync_notes(memory,notes,distilled=distilled)
    document=next(d for d in memory.documents()['documents'] if d['title']=='Duty approval')
    assert document['distilled']==[] and not document['distilled_import_hash']


def test_invalid_sidecar_does_not_hide_raw_source_or_leave_stale_entries(memory,tmp_path):
    notes,distilled,source,sidecar=fixture(tmp_path)
    sync_notes(memory,notes,distilled=distilled)
    source.write_text('# Duty approval\nOmar now approves invoices above £900.\n')
    result=sync_notes(memory,notes,distilled=distilled)
    assert any(e['stage']=='distilled-file' for e in result['errors'])
    document=next(d for d in memory.documents()['documents'] if d['title']=='Duty approval')
    assert not document['distilled']
    assert any('Omar now approves' in h['quote'] for h in memory.search('invoices')['hits'])


def test_sidecar_cannot_reference_a_path_outside_the_note_root(memory,tmp_path):
    notes,distilled,_,sidecar=fixture(tmp_path)
    sidecar.write_text(sidecar.read_text().replace('daily/approval.md','../outside.md'))
    result=sync_notes(memory,notes,distilled=distilled)
    assert result['errors'] and result['updated']==1
    assert not any(d['distilled'] for d in memory.documents()['documents'])


def test_whitespace_normalized_quote_keeps_actual_source_offsets(memory,tmp_path):
    notes,distilled,source,_=fixture(tmp_path)
    source.write_text('# Duty approval\nNila approves invoices above £600\nonly in November.\n')
    result=sync_notes(memory,notes,distilled=distilled)
    assert not result['errors']
    document=next(d for d in memory.documents()['documents'] if d['title']=='Duty approval')
    assert '\n' in document['distilled'][0]['quote']
    assert document['distilled'][0]['quote'] in document['text']


def test_rag_hook_can_recall_five_note_hits(memory,tmp_path):
    for i in range(6):(tmp_path/f'{i}.md').write_text(f'# Rule {i}\nThe obsidian invoice rule number {i}.\n')
    sync_notes(memory,tmp_path)
    result=memory.recall('obsidian invoice',mode='rag')
    assert result['trace']['note_hits']==5


def test_chunk_embedding_includes_relative_source_label():
    document={'id':'opaque-id','title':'Invoice rules','origin':'markdown:root/daily/approval.md',
        'text':'# Limits\nNila approves invoices.','distilled':[{'question':'Who signs?',
        'summary':'Nila','quote':'Nila approves invoices.'}]}
    result=chunks(document)
    assert all('[daily/approval.md § ' in c['text'] for c in result)


def test_equal_fusion_scores_use_semantic_rank_instead_of_opaque_source_id():
    assert fuse({'keyword':['aaa'],'meaning':['zzz']})[0]==['zzz','aaa']


def test_notes_root_itself_is_not_excluded_as_a_nested_implementation(tmp_path,monkeypatch):
    import memory_lab.notes as notes
    source=tmp_path/'source.md';source.write_text('# A genuine note\nThe source remains discoverable.\n')
    monkeypatch.setattr(notes,'PROJECT_ROOT',tmp_path)
    assert notes.note_files(tmp_path)==[source]
