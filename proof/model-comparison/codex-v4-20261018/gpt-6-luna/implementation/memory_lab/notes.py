"""Failure-isolated Markdown import, grounded processing and atomic publication."""
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from filelock import FileLock

from .domain import Document
from .imports import kind_name,reference_extraction,extraction_files,extraction_entities,normalized
from .model_tasks import distil_document,extract_document
from .settings import ROOT,settings
from .state import atomic_json
from .distilled_import import read_entries,apply_entries

PROJECT_ROOT=ROOT
SKIP={'.git','node_modules','__pycache__','.venv','.memory'}


def note_files(folder):
    root=Path(folder).resolve()
    if not root.is_dir():raise ValueError('Notes folder does not exist')
    own=Path(PROJECT_ROOT).resolve()
    return [p for p in sorted(root.rglob('*.md')) if not p.is_symlink() and
        not (own.is_relative_to(root) and p.is_relative_to(own)) and
        not any(part.startswith('.') or part in SKIP for part in p.relative_to(root).parts)]


def metadata(text):
    if not text.startswith('---\n'):return {},''
    import yaml
    match=re.match(r'^---\n(.*?)\n---(?:\n|$)',text,re.S)
    if not match:raise ValueError('Unclosed Markdown front matter')
    try:parsed=yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:raise ValueError('Invalid Markdown metadata') from exc
    if parsed is not None and not isinstance(parsed,dict):raise ValueError('Front matter must be a mapping')
    return parsed or {},match.group(0)


def read_note(path,root,*,ignore_metadata=False):
    text=path.read_text(encoding='utf-8')
    meta={} if ignore_metadata else metadata(text)[0]
    title=meta.get('title') or next((m.group(1).strip() for m in re.finditer(r'(?m)^#\s+(.+)$',text)),path.stem)
    rel=path.relative_to(root).as_posix();origin='markdown:'+hashlib.sha256(str(root).encode()).hexdigest()[:16]+'/'+rel
    id='note-'+uuid.uuid5(uuid.NAMESPACE_URL,origin).hex
    entities=meta.get('entities',[])
    annotated=entities if isinstance(entities,list) and all(isinstance(e,dict) for e in entities) else []
    if not annotated:annotated=[{'id':id,'kind':kind_name(meta.get('type','Document')),'name':str(title),'aliases':[path.stem,rel]}]
    return Document.model_validate({'id':id,'title':str(title),'text':text,'origin':origin,
        'entities':annotated,'facts':meta.get('facts',[])})


def _source_entity(document,meta):
    candidates=[e for e in document.entities if normalized(e.name)==normalized(document.title)
        and e.kind==kind_name(meta['meta'].get('type','Document'))]
    if len(candidates)==1:
        return candidates[0].model_dump()|{'description':''}
    return {'id':document.id,'kind':kind_name(meta['meta'].get('type','Document')),'name':document.title,'aliases':[]}


def _references(documents,metas,errors):
    by_label={}
    for id,d in documents.items():
        for label in [d.title,metas[id]['relative'],Path(metas[id]['relative']).stem]:
            by_label.setdefault(normalized(label),set()).add(id)
    for id,d in list(documents.items()):
        links=metas[id]['meta'].get('links',[])
        if not links:continue
        if not isinstance(links,list):
            errors.append({'path':metas[id]['path'],'stage':'links','error':'ValueError'});continue
        data=d.model_dump();entities={e['id']:e for e in data['entities']};facts=list(data['facts'])
        # Front-matter links reference source documents, not guessed named entities.
        own=_source_entity(d,metas[id])
        entities.setdefault(own['id'],own)
        for link in links:
            matches=by_label.get(normalized(link),set())|by_label.get(normalized(Path(str(link)).stem),set())
            if len(matches)!=1:
                errors.append({'path':metas[id]['path'],'stage':'link-target','error':'UnresolvedReference'});continue
            target_id=next(iter(matches));target=documents[target_id]
            target_entity=_source_entity(target,metas[target_id])
            entities.setdefault(target_entity['id'],target_entity)
            fact={'subject':own['id'],'predicate':'REFERENCES','object':target_entity['id'],'quote':metas[id]['front']}
            if fact not in facts:facts.append(fact)
        try:documents[id]=Document.model_validate(data|{'entities':list(entities.values()),'facts':facts})
        except ValueError as exc:errors.append({'path':metas[id]['path'],'stage':'links','error':type(exc).__name__})


def _checkpoint(document,phase,known,cache,caller):
    cfg=settings()
    # Include the processing implementation so prompt/schema changes invalidate old results.
    implementation=(ROOT/'memory_lab/model_tasks.py').read_bytes()
    content={'document':document.model_dump(),'phase':phase,'known':known if phase=='extract' else [],
        'provider':cfg.write_provider,'model':cfg.digest_model,'write_model':os.environ.get('MEMORY_WRITE_MODEL'),'codex_model':os.environ.get('MEMORY_CODEX_MODEL'),
        'claude_model':os.environ.get('MEMORY_CLAUDE_MODEL'),
        'implementation':hashlib.sha256(implementation).hexdigest()}
    key=hashlib.sha256(json.dumps(content,sort_keys=True).encode()).hexdigest();path=cache/(key+'.json')
    if path.exists():
        try:
            record=json.loads(path.read_text());result=Document.model_validate(record['document'])
            if result.id==document.id and result.text==document.text:return result,record['usage'],True
        except (ValueError,KeyError,TypeError):pass
    kwargs={'caller':caller} if caller else {}
    result,usage=distil_document(document,**kwargs) if phase=='distil' else extract_document(document,known,**kwargs)
    atomic_json(path,{'document':result.model_dump(),'usage':usage})
    return result,usage,False


def sync_notes(memory,folder,*,distil=False,extract=False,caller=None,limit=None,cache_dir=None,extractions=None,distilled=None):
    root=Path(folder).expanduser().resolve();cfg=settings()
    cache=Path(cache_dir) if cache_dir else cfg.state_dir/'processing'/memory.namespace
    cache.mkdir(parents=True,exist_ok=True)
    lock=hashlib.sha256(str(root).encode()).hexdigest()
    with FileLock(str(cache/(lock+'.lock')),timeout=180):
        return _sync_notes(memory,root,distil,extract,caller,cfg.process_limit if limit is None else limit,cache,extractions,distilled)


def _sync_notes(memory,root,distil,extract,caller,limit,cache,extractions,distilled):
    if type(limit) is not int or limit<0:raise ValueError('Processing limit must be nonnegative')
    prefix='markdown:'+hashlib.sha256(str(root).encode()).hexdigest()[:16]+'/'
    current=memory.documents();previous={d['id']:Document.model_validate(d) for d in current['documents']}
    documents=dict(previous);known=memory.entities();seen=set();fresh={};metas={};errors=[];usage=[];reused_usage=[];work=0;deferred=[]
    extracted,import_errors=extraction_files(extractions);errors.extend(import_errors)
    distilled_root=Path(distilled).expanduser().resolve() if distilled is not None else None
    if distilled_root==root:raise ValueError('Distilled sidecars need a separate folder from raw notes')
    for path in note_files(root):
        if distilled_root and path.is_relative_to(distilled_root):continue
        try:
            d=read_note(path,root);meta,front=metadata(d.text)
        except (ValueError,TypeError,KeyError,OSError) as exc:
            errors.append({'path':str(path),'stage':'metadata','error':type(exc).__name__})
            # Broken metadata remains searchable. Unreadable/oversized sources
            # retain their prior indexed revision rather than blocking the folder.
            try:d=read_note(path,root,ignore_metadata=True);meta={};front=''
            except (ValueError,TypeError,KeyError,OSError):
                origin=prefix+path.relative_to(root).as_posix()
                seen.update(id for id,old in previous.items() if old.origin==origin)
                continue
        if not d.text.strip():continue
        seen.add(d.id);fresh[d.id]=d
        metas[d.id]={'meta':meta,'front':front,'path':str(path),'relative':path.relative_to(root).as_posix()}
    known=extraction_entities(extracted,known)
    for id,d in list(fresh.items()):
        payload=extracted.get(metas[id]['relative']) or extracted.get(Path(metas[id]['relative']).name)
        if not payload:continue
        try:fresh[id]=reference_extraction(d,payload,known)
        except (ValueError,KeyError,TypeError) as exc:
            errors.append({'path':metas[id]['path'],'stage':'extraction-file','error':type(exc).__name__})
    # Two passes allow links and typed endpoints to point to later files.
    _references(fresh,metas,errors)
    if distilled_root is not None:
        imported,import_errors=read_entries(distilled_root,{metas[id]['relative']:d for id,d in fresh.items()})
        errors.extend(import_errors)
        for id,d in list(fresh.items()):
            entries=imported.get(metas[id]['relative'])
            if entries:fresh[id]=apply_entries(d,entries)
    updated=0;skipped=0;states={}
    for id,raw in fresh.items():
        d=raw;old=previous.get(id)
        payload=extracted.get(metas[id]['relative']) or extracted.get(Path(metas[id]['relative']).name)
        # Preserve completed work only for unchanged sources/explicit annotations.
        if old and old.text==d.text and (not payload or (old.entities==d.entities and old.facts==d.facts)):
            if distilled_root is not None and old.distilled_import_hash!=d.distilled_import_hash:
                flags=(set(old.processed)-{'distilled'})|({'distilled'} if d.distilled else set())
                d=Document.model_validate(old.model_dump()|{'distilled':d.distilled,
                    'distilled_import_hash':d.distilled_import_hash,'processed':sorted(flags)})
            else:d=old
        needs=(distil and 'distilled' not in d.processed) or (extract and 'extracted' not in d.processed)
        if needs and work>=limit:deferred.append(id)
        elif needs:
            work+=1
            for phase,enabled,flag in [('distil',distil,'distilled'),('extract',extract,'extracted')]:
                if not enabled or flag in d.processed:continue
                try:
                    d,u,reused=_checkpoint(d,phase,known,cache,caller)
                    (reused_usage if reused else usage).append(u)
                    if phase=='extract':known.extend(e.model_dump() for e in d.entities)
                except Exception as exc:
                    errors.append({'path':metas[id]['path'],'stage':phase,'error':type(exc).__name__})
        documents[id]=d;states[id]={'path':metas[id]['path'],'processed':d.processed}
        if old and old==d:skipped+=1
        else:updated+=1
    modeled={id:documents[id] for id in fresh}
    for id,d in list(modeled.items()):
        if metas[id]['meta'].get('links'):
            data=d.model_dump();front=metas[id]['front']
            removed_refs=[f for f in data['facts'] if f['predicate']=='REFERENCES' and f['quote']==front]
            borrowed={f['object'] for f in removed_refs if f['object'].startswith('note-')}
            data['facts']=[f for f in data['facts'] if f not in removed_refs]
            live={f[k] for f in data['facts'] for k in ['subject','object']}
            data['entities']=[e for e in data['entities'] if e['id'] not in borrowed or e['id'] in live or e['id']==id]
            modeled[id]=Document.model_validate(data)
    _references(modeled,metas,errors)
    for id,d in modeled.items():documents[id]=d
    removed=[id for id,d in documents.items() if d.origin.startswith(prefix) and id not in seen]
    for id in removed:del documents[id]
    result=memory.replace_documents([d.model_dump() for d in documents.values()],current['snapshot'])
    return {**result,'updated':updated,'unchanged':skipped,'removed':len(removed),'model_usage':usage,
        'errors':errors,'deferred':deferred,'documents':states,'reused_model_usage':reused_usage}
