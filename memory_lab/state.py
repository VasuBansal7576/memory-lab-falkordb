"""Durable JSON replacement shared by capture and write-time checkpoints."""
import json
import os
from pathlib import Path
import tempfile


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',dir=path.parent,delete=False,encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno());temp=f.name
    os.replace(temp,path)
