"""In-process job registry for long-running operations (v2.0).

Used by ingest / podcast / OCR workflows: the producer creates a job, runs the
work on a background thread and reports progress with `update_job`; the SPA
polls `GET /jobs/{id}` and can request cancellation with
`POST /jobs/{id}/cancel` (worker threads check `cancel_event(job_id)`).

Jobs live in memory only — restarting the API clears the history.
"""
from __future__ import annotations

import threading
import time
import uuid

_lock = threading.Lock()
_jobs: dict[str, dict] = {}

_MAX_JOBS = 100          # hard cap of retained (any status) jobs
_KEEP_DONE = 40          # how many completed/failed/cancelled jobs to keep at most

TERMINAL_STATUSES = ("completed", "failed", "cancelled")


def _now() -> float:
    return time.time()


def _public(job: dict) -> dict:
    """Copy of a job without thread-only private keys."""
    return {k: v for k, v in job.items() if not k.startswith("_")}


def _prune_locked() -> None:
    if len(_jobs) <= _MAX_JOBS:
        return
    done = sorted(
        (jid for jid, j in _jobs.items() if j["status"] in TERMINAL_STATUSES),
        key=lambda jid: _jobs[jid]["created_at"],
    )
    keep = max(0, _MAX_JOBS - len(_jobs))
    for jid in done[: max(0, len(done) - keep) or len(done)]:
        _jobs.pop(jid, None)
    # keep at most _KEEP_DONE terminal jobs overall
    done = sorted(
        (jid for jid, j in _jobs.items() if j["status"] in TERMINAL_STATUSES),
        key=lambda jid: _jobs[jid]["created_at"],
        reverse=True,
    )
    for jid in done[_KEEP_DONE:]:
        _jobs.pop(jid, None)


def create_job(kind: str, label: str = "", payload: dict | None = None) -> dict:
    """Register a new job and return its public shape (id included)."""
    job = {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,
        "label": label,
        "status": "queued",
        "progress": None,           # 0.0..1.0 when known, None = indeterminate
        "message": "",
        "payload": payload or {},
        "result": None,
        "error": None,
        "cancel_requested": False,
        "created_at": _now(),
        "updated_at": _now(),
        # private, thread-only keys (never serialised)
        "_cancel_event": threading.Event(),
    }
    with _lock:
        _jobs[job["id"]] = job
        _prune_locked()
    return _public(job)


def get_job(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return _public(dict(job)) if job else None


def list_jobs() -> list[dict]:
    with _lock:
        jobs = [_public(job) for job in _jobs.values()]
    jobs.sort(key=lambda j: j["created_at"], reverse=True)
    return jobs


def update_job(
    job_id: str,
    *,
    status: str | None = None,
    progress: float | None = None,
    message: str | None = None,
    result=None,
    error: str | None = None,
) -> dict | None:
    """Progress-style update from the worker thread."""
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        if status in TERMINAL_STATUSES:
            job["_cancel_event"].set()   # release any waiters
        if status is not None:
            job["status"] = status
        if progress is not None:
            job["progress"] = max(0.0, min(float(progress), 1.0))
        if message is not None:
            job["message"] = message
        if result is not None:
            job["result"] = result
        if error is not None:
            job["error"] = error
        job["updated_at"] = _now()
        return _public(dict(job))


def request_cancel(job_id: str) -> bool:
    """Ask a running job to stop. Returns False when the job is unknown or
    already finished."""
    with _lock:
        job = _jobs.get(job_id)
        if not job or job["status"] in TERMINAL_STATUSES:
            return False
        job["cancel_requested"] = True
        job["_cancel_event"].set()
        job["updated_at"] = _now()
        return True


def cancel_event(job_id: str) -> threading.Event | None:
    """The worker's cancellation handle (`event.is_set()` => stop)."""
    with _lock:
        job = _jobs.get(job_id)
        return job["_cancel_event"] if job else None


def finish(job_id: str, *, result=None, error: str | None = None) -> dict | None:
    """Mark a job terminal in one call."""
    if error:
        return update_job(job_id, status="failed", error=error)
    return update_job(job_id, status="completed", progress=1.0, result=result)
