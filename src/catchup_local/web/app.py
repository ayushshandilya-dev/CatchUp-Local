"""FastAPI application for CatchUp Local.

Uploads are parsed in memory and analysed in a background thread. Jobs and
briefings live only in process memory; nothing is written to disk by a live
run and nothing is sent off the machine.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from catchup_local.config import (
    ALLOWED_EXTENSIONS,
    EXAMPLES_DIR,
    MAX_MESSAGES,
    MAX_UPLOAD_BYTES,
    SAMPLE_CHAT,
    Settings,
    load_settings,
)
from catchup_local.llm.ollama import OllamaClient
from catchup_local.llm.pipeline import BriefingPipeline, PipelineError
from catchup_local.parser.model import ParseError
from catchup_local.parser.parser import parse_export


WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
SAMPLE_BRIEFING = EXAMPLES_DIR / "sample_briefing.json"
MAX_JOBS = 20
JOB_TTL_SECONDS = 60 * 60
logger = logging.getLogger("catchup_local.web")


@dataclass
class Job:
    job_id: str
    source_name: str
    status: str = "queued"
    phase: str = "queued"
    done: int = 0
    total: int = 1
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job_id": self.job_id,
            "source_name": self.source_name,
            "status": self.status,
            "phase": self.phase,
            "done": self.done,
            "total": self.total,
            "error": (
                {"code": self.error_code, "message": self.error_message}
                if self.error_code
                else None
            ),
        }
        if self.status == "done":
            payload["result"] = self.result
        return payload


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, source_name: str) -> Job:
        job = Job(job_id=secrets.token_urlsafe(9), source_name=source_name)
        with self._lock:
            self._prune_locked()
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def _prune_locked(self) -> None:
        now = time.time()
        stale = [
            key
            for key, job in self._jobs.items()
            if job.finished_at and now - job.finished_at > JOB_TTL_SECONDS
        ]
        for key in stale:
            self._jobs.pop(key, None)
        if len(self._jobs) >= MAX_JOBS:
            completed = sorted(
                (job for job in self._jobs.values() if job.finished_at),
                key=lambda job: job.finished_at or 0,
            )
            for job in completed[: max(1, len(self._jobs) - MAX_JOBS + 1)]:
                self._jobs.pop(job.job_id, None)


def _safe_name(filename: str | None) -> str:
    name = Path(filename or "chat.txt").name
    cleaned = "".join(char for char in name if char.isprintable()).strip()
    return (cleaned or "chat.txt")[:120]


def _read_sample(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        logger.warning("Precomputed sample briefing is unreadable.")
        return None


def create_app(
    settings: Settings | None = None,
    client: OllamaClient | None = None,
    sample_path: Path | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    client = client or OllamaClient(settings)
    sample_file = sample_path or SAMPLE_BRIEFING
    jobs = JobStore()
    app = FastAPI(title="CatchUp Local", docs_url=None, redoc_url=None)

    def start_job(parsed: Any, source_name: str) -> Job:
        job = jobs.create(source_name)

        def progress(phase: str, done: int, total: int) -> None:
            job.phase = phase
            job.done = done
            job.total = max(1, total)
            if job.status == "queued":
                job.status = "running"

        def run() -> None:
            job.status = "running"
            try:
                result = BriefingPipeline(client, settings).analyze(parsed, progress=progress)
                job.result = result
                job.status = "done"
                job.phase = "done"
                job.done = job.total
            except PipelineError as error:
                job.status = "error"
                job.error_code = error.code
                job.error_message = error.message
                logger.warning("Analysis failed: %s", error.code)
            except Exception:
                job.status = "error"
                job.error_code = "analysis_failed"
                job.error_message = (
                    "The analysis failed unexpectedly. Nothing was sent online. "
                    "Check that Ollama is running and the model is installed."
                )
                logger.warning("Analysis failed unexpectedly.")
            finally:
                job.finished_at = time.time()

        threading.Thread(target=run, name=f"catchup-{job.job_id}", daemon=True).start()
        return job

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        status = client.health()
        return {
            "ollama": status,
            "sample_available": sample_file.exists(),
            "model": settings.model,
            "limits": {
                "max_upload_bytes": MAX_UPLOAD_BYTES,
                "max_messages": MAX_MESSAGES,
            },
        }

    @app.get("/api/sample")
    def sample() -> JSONResponse:
        payload = _read_sample(sample_file)
        if payload is None:
            raise HTTPException(status_code=404, detail="The precomputed sample is not available.")
        return JSONResponse(payload)

    @app.post("/api/analyze", status_code=202)
    async def analyze(file: UploadFile = File(...)) -> dict[str, Any]:
        name = _safe_name(file.filename)
        if not any(name.casefold().endswith(ext) for ext in ALLOWED_EXTENSIONS):
            raise HTTPException(
                status_code=400,
                detail="Please upload a WhatsApp chat export as a .txt file.",
            )
        raw = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail="That export is larger than the 8 MB limit.",
            )
        try:
            parsed = parse_export(raw, source_name=name)
        except ParseError as error:
            raise HTTPException(status_code=400, detail=error.message) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        if not parsed.messages:
            raise HTTPException(status_code=400, detail="No messages were found in that export.")
        if len(parsed.messages) > MAX_MESSAGES:
            raise HTTPException(
                status_code=413,
                detail=f"That export has more than {MAX_MESSAGES:,} messages.",
            )
        job = start_job(parsed, name)
        return {"job_id": job.job_id}

    @app.post("/api/analyze-sample", status_code=202)
    def analyze_sample() -> dict[str, Any]:
        if not SAMPLE_CHAT.exists():
            raise HTTPException(status_code=404, detail="The sample chat is not available.")
        parsed = parse_export(SAMPLE_CHAT.read_bytes(), source_name=SAMPLE_CHAT.name)
        job = start_job(parsed, SAMPLE_CHAT.name)
        return {"job_id": job.job_id}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="That analysis job is no longer available.")
        return job.to_payload()

    return app


app = create_app()
