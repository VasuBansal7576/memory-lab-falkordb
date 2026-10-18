"""Read the reference's Markdown sidecars, retaining exact source evidence."""
from pathlib import Path, PurePosixPath
import hashlib
import json
import re

from .domain import Document


def read_entries(folder,documents):
    root=Path(folder).expanduser().resolve()
    if not root.is_dir():raise ValueError('Distilled folder does not exist')
    entries={};errors=[]
    for path in sorted(root.rglob('*.md')):
        if path.is_symlink() or any(p.startswith('.') for p in path.relative_to(root).parts):continue
        try:
            fields={};questions=[]
            for line in path.read_text(encoding='utf-8').splitlines():
                if line.startswith('- '):questions.append(line[2:].strip())
                elif ':' in line:
                    key,value=line.split(':',1)
                    if key in {'source','summary','rule','quote'}:
                        if key in fields:raise ValueError('Repeated sidecar field')
                        fields[key]=value.strip()
            source=fields.get('source','').replace('\\','/')
            relative=PurePosixPath(source)
            if not source or relative.is_absolute() or '..' in relative.parts or ':' in source:
                raise ValueError('Sidecar source must be inside the notes folder')
            source=relative.as_posix()
            if source not in documents:raise ValueError('Sidecar source is missing')
            quote=fields.get('quote','')
            if len(quote)>=2 and quote[0]==quote[-1] and quote[0] in {'"',"'"}:quote=quote[1:-1]
            if not quote.strip() or not questions or not all(questions):raise ValueError('Missing question or quote')
            text=documents[source].text
            if quote not in text:
                match=re.search(r'\s+'.join(re.escape(t) for t in quote.split()),text)
                if match is None:raise ValueError('Quote does not occur in source')
                quote=match.group(0)
            imported=[{'question':q,'summary':fields.get('summary',''),'rule':fields.get('rule',''),'quote':quote} for q in questions]
            combined=entries.get(source,[])+imported
            # Validate size/schema limits before adding any part of this file.
            Document.model_validate(documents[source].model_dump()|{'distilled':combined})
            entries[source]=combined
        except (ValueError,OSError,TypeError) as exc:
            errors.append({'path':str(path),'stage':'distilled-file','error':type(exc).__name__})
    return entries,errors


def apply_entries(document,entries):
    fingerprint=hashlib.sha256(json.dumps(entries,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return Document.model_validate(document.model_dump()|{'distilled':entries,
        'distilled_import_hash':fingerprint,'processed':sorted(set(document.processed)|{'distilled'})})
