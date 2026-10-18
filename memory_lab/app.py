import os
import time
from threading import Lock
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .domain import Document
from .generation import GenerationError, configuration, generate, codex_configuration, generate_codex
from .memory import Memory, MemoryConflict, MemoryUnavailable
from .settings import settings,open_memory
from .sessions import create_session,append_exchange,close_session,spawn_worker
from .notes import sync_notes

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=100000)
    mode: Literal["evidence", "anthropic", "codex"] = "evidence"
    session_id: str | None = Field(default=None,max_length=100)


class ReplaceRequest(BaseModel):
    document: Document
    expected_snapshot: str


class SaveRequest(BaseModel):
    question: str = Field(min_length=1, max_length=100000)


class ResumeRequest(BaseModel):
    mode: Literal["evidence", "anthropic", "codex"] = "evidence"
    session_id: str | None = Field(default=None,max_length=100)


class RecallRequest(BaseModel):
    question: str = Field(min_length=1, max_length=100000)
    hops: int = Field(default=3, ge=1, le=3)
    top_k: int = Field(default=8, ge=1, le=32)


def create_app(memory: Memory | None = None, seed: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.memory = memory
        application.state.initialization_lock = Lock()
        try:
            storage()
        except MemoryUnavailable:
            pass
        yield

    application = FastAPI(title="Memory Lab", lifespan=lifespan)

    def storage() -> Memory:
        with application.state.initialization_lock:
            if application.state.memory is None:
                cfg=settings()
                candidate = Memory(host=cfg.host,port=cfg.port,namespace=cfg.namespace)
                if seed:
                    candidate.ensure_seed(ROOT / "data" / "operations.json")
                application.state.memory = candidate
            return application.state.memory

    @application.exception_handler(MemoryUnavailable)
    async def database_error(request, exc):
        return JSONResponse(status_code=503, content={"detail": "FalkorDB is unavailable. Start the database and retry."})

    @application.exception_handler(MemoryConflict)
    async def conflict_error(request, exc):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @application.exception_handler(GenerationError)
    async def generation_error(request, exc):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    def answer(question: str, mode: str, session_id: str | None = None) -> dict:
        started = time.perf_counter()
        result = storage().recall(question.strip()) if mode == "codex" else storage().ask(question.strip())
        if mode == "anthropic":
            result = generate(result)
        if mode == "codex":
            result = generate_codex(result)
        if storage().overview()["snapshot"] != result["snapshot"]:
            raise HTTPException(409, "Knowledge changed while answering. Ask again to use the current evidence.")
        if session_id and mode!='evidence':
            try:
                append_exchange(session_id,question,result['answer'])
            except (FileNotFoundError,ValueError):
                raise HTTPException(409,'Memory session expired; start a new session and retry.')
        return {**result, "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}

    @application.get("/api/status")
    def status():
        model = configuration()
        codex = codex_configuration()
        try:
            return {"database": "connected", **storage().overview(), "model": model, "codex": codex,
                    "answer_modes": ["evidence", *(["anthropic"] if model["configured"] else []), *(["codex"] if codex["configured"] else [])]}
        except MemoryUnavailable:
            return JSONResponse(status_code=503, content={"database": "unavailable", "model": model,
                                "detail": "Start FalkorDB with docker compose -p graph-memory-lab up -d --wait."})

    @application.get("/api/documents")
    def documents():
        return storage().documents()

    @application.get("/api/entities")
    def entities():
        return storage().entities()

    @application.post("/api/ask")
    def ask(body: AskRequest):
        if not body.question.strip():
            raise HTTPException(422, "Enter a question.")
        return answer(body.question, body.mode, body.session_id)

    @application.post("/api/recall")
    def recall(body: RecallRequest):
        if not body.question.strip():
            raise HTTPException(422, "Enter a question.")
        return storage().recall(body.question.strip(), body.hops, body.top_k)

    @application.put("/api/documents/{document_id}")
    def replace_document(document_id: str, body: ReplaceRequest):
        if document_id != body.document.id:
            raise HTTPException(422, "The document ID must match the URL.")
        try:
            return storage().replace_document(body.document, body.expected_snapshot)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @application.get("/api/investigations")
    def investigations():
        return storage().investigations()

    @application.post("/api/investigations")
    def save_investigation(body: SaveRequest):
        if not body.question.strip():
            raise HTTPException(422, "Enter a question.")
        return storage().save_investigation(body.question.strip())

    @application.post("/api/investigations/{investigation_id}/resume")
    def resume(investigation_id: str, body: ResumeRequest):
        try:
            saved = storage().get_investigation(investigation_id)
        except KeyError as exc:
            raise HTTPException(404, "Saved investigation not found.") from exc
        result = answer(saved["question"], body.mode, body.session_id)
        result["resumed_from_snapshot"] = saved["snapshot"]
        return result

    @application.post('/api/sessions')
    def start_session():
        return {'id':create_session()}

    @application.post('/api/sessions/{session_id}/close')
    def end_session(session_id:str):
        try:
            return close_session(session_id)
        except (ValueError,FileNotFoundError):
            raise HTTPException(404,'Session not found')

    @application.get('/api/memory/status')
    def memory_status():
        import json
        cfg=settings(); jobs=[]
        for path in sorted((cfg.state_dir/'pending').glob('*.json')):
            try:
                d=json.loads(path.read_text());jobs.append({k:d.get(k) for k in ('key','status','error','note_path','snapshot')})
            except ValueError:continue
        return {'jobs':jobs,'notes_dir':str(cfg.notes_dir),'capture_mode':os.environ.get('MEMORY_CAPTURE_MODE','headless')}

    @application.post('/api/memory/process')
    def retry_memory():
        spawn_worker()
        return {'status':'queued'}

    @application.post('/api/notes/sync')
    def index_memory_notes():
        cfg=settings();cfg.notes_dir.mkdir(parents=True,exist_ok=True)
        return sync_notes(storage(),cfg.notes_dir)

    @application.post('/api/search')
    def search_notes(body:RecallRequest):
        return storage().search(body.question,min(body.top_k,10))

    @application.get("/")
    def index():
        return FileResponse(ROOT / "memory_lab" / "web" / "index.html")

    @application.get("/proof")
    def proof():
        return FileResponse(ROOT / "memory_lab" / "web" / "proof.html")

    application.mount("/proof-data", StaticFiles(directory=ROOT / "proof", check_dir=False), name="proof-data")
    application.mount("/static", StaticFiles(directory=ROOT / "memory_lab" / "web", check_dir=False), name="static")
    return application


app = create_app()
