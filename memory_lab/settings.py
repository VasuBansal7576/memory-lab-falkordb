"""One configuration path for the web app, CLIs, hooks and workers."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    namespace: str
    notes_dir: Path
    state_dir: Path
    semantic: bool
    context_chars: int = 6000
    note_context_chars: int = 1500
    note_hit_chars: int = 280
    neighbor_lines: int = 6
    max_turns: int = 30
    max_chars: int = 15000
    min_turns: int = 2
    distil: bool = True
    auto_extract: bool = True
    capture_mode: str = 'headless'
    process_limit: int = 20
    digest_prompt: Path = ROOT/'memory_lab/prompts/digest.txt'
    log_dir: str = ''
    write_provider: str = 'codex'
    digest_model: str | None = None
    recall_mode: str = 'hybrid'
    vector_max_distance: float | None = None


def _flag(name,default=True):
    return os.environ.get(name,'1' if default else '0').casefold() not in {'0','false','off','no'}


def _integer(name,default,minimum=0):
    value=int(os.environ.get(name,str(default)))
    if value<minimum:raise ValueError(name+' is below its minimum')
    return value


def settings(env_file=None):
    load_dotenv(Path(env_file) if env_file else ROOT/'.env', override=False)
    state = Path(os.environ.get('MEMORY_STATE_DIR',str(ROOT/'.memory'))).expanduser().resolve()
    mode=os.environ.get('MEMORY_CAPTURE_MODE','headless')
    mode='next_session' if mode=='session' else mode
    if mode not in {'headless','next_session','off'}:raise ValueError('Invalid capture mode')
    provider=os.environ.get('MEMORY_WRITE_PROVIDER','codex')
    if provider not in {'codex','claude'}:raise ValueError('Invalid write provider')
    recall_mode=os.environ.get('MEMORY_RECALL_MODE','hybrid')
    if recall_mode not in {'graph','rag','hybrid'}:raise ValueError('Invalid recall mode')
    distance=os.environ.get('MEMORY_VECTOR_MAX_DISTANCE')
    distance=float(distance) if distance else None
    if distance is not None and not 0<=distance<=2:raise ValueError('Vector distance must be between zero and two')
    log_dir=os.environ.get('MEMORY_LOG_DIR','')
    if Path(log_dir).is_absolute() or '..' in Path(log_dir).parts:raise ValueError('Log directory must be inside the notes folder')
    return Settings(os.environ.get('FALKORDB_HOST','127.0.0.1'),
        _integer('FALKORDB_PORT',6387,1),os.environ.get('MEMORY_NAMESPACE','graph_memory_lab_v2'),
        Path(os.environ.get('MEMORY_NOTES_DIR',str(state/'notes'))).expanduser().resolve(),state,_flag('MEMORY_SEMANTIC'),
        context_chars=_integer('MEMORY_CONTEXT_CHARS',6000,256),
        note_context_chars=_integer('MEMORY_NOTE_CONTEXT_CHARS',1500),
        note_hit_chars=_integer('MEMORY_NOTE_HIT_CHARS',280),
        neighbor_lines=_integer('MEMORY_NEIGHBOR_LINES',6),
        max_turns=_integer('MEMORY_MAX_TURNS',30,1),max_chars=_integer('MEMORY_MAX_CHARS',15000,1),
        min_turns=_integer('MEMORY_MIN_TURNS',2,1),distil=_flag('MEMORY_DISTIL'),
        auto_extract=_flag('MEMORY_AUTO_EXTRACT'),capture_mode=mode,
        process_limit=_integer('MEMORY_PROCESS_LIMIT',20),
        digest_prompt=Path(os.environ.get('MEMORY_DIGEST_PROMPT',str(ROOT/'memory_lab/prompts/digest.txt'))).expanduser().resolve(),
        log_dir=log_dir,write_provider=provider,digest_model=os.environ.get('MEMORY_DIGEST_MODEL') or None,recall_mode=recall_mode,vector_max_distance=distance)


def open_memory(env_file=None, seed=True):
    from .memory import Memory
    cfg=settings(env_file)
    memory=Memory(host=cfg.host,port=cfg.port,namespace=cfg.namespace)
    if seed:memory.ensure_seed(ROOT/'data/operations.json')
    return memory
