"""FastAPI application – the API layer of the Commentarium System."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.logging_setup import get_recent_logs, clear_logs, setup_logging
from app.db.metadata import MetadataStore
from app.db.vectorstore import VectorStore
from app.db.chat import ChatStore
from app.ingest.pipeline import ingest_document, store_upload


def _chat_store() -> ChatStore:
    return ChatStore()

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_settings()
    setup_logging(cfg.log_level)
    logging.getLogger("app").info("Commentarium API starting  host=%s port=%d", cfg.host, cfg.port)
    yield
    logging.getLogger("app").info("Commentarium API shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Commentarium API",
    description="RAG system for thesis research – upload PDFs & Word docs, chat with them, get accurate citations.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger("app")


# ---------------------------------------------------------------------------
# Global exception handler (logs full traceback)
# ---------------------------------------------------------------------------

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)},
    )


# ---------------------------------------------------------------------------
# Health / status
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Simple liveness probe."""
    cfg = get_settings()
    return {
        "status": "ok",
        "version": app.version,
        "port": cfg.port,
        "has_openrouter_key": cfg.has_openrouter_key,
        "embedding_backend": cfg.embedding_backend,
        "ocr_backend": cfg.ocr_backend,
    }


@app.get("/settings/summary")
async def settings_summary():
    """Non-sensitive configuration overview."""
    cfg = get_settings()
    return {
        "chat_model": cfg.chat_model or "(not set)",
        "embedding_backend": cfg.embedding_backend,
        "embedding_model": cfg.local_embedding_model if cfg.embedding_backend == "local" else cfg.embedding_model,
        "ocr_backend": cfg.ocr_backend,
        "citation_style": cfg.citation_style,
        "top_k": cfg.top_k,
        "chunk_size": cfg.chunk_size,
        "chat_temperature": cfg.chat_temperature,
        "paddleocr_vl_model": cfg.paddleocr_vl_model,
        "paddleocr_vl_base_url": cfg.paddleocr_vl_base_url,
        "teleocr_model": cfg.teleocr_model,
        "teleocr_base_url": cfg.teleocr_base_url,
        "tts_provider": cfg.tts_provider,
        "tts_api_url": cfg.tts_api_url,
        "tts_api_model": cfg.tts_api_model,
        "tts_api_format": cfg.tts_api_format,
        "voicestudio_url": cfg.voicestudio_url,
        "voicestudio_model": cfg.voicestudio_model,
        "voicestudio_format": cfg.voicestudio_format,
        "cf_model": cfg.cf_model,
        "google_language_code": cfg.google_language_code,
        "podcast_num_hosts": cfg.podcast_num_hosts,
    }


# ---------------------------------------------------------------------------
# Settings v2 – full configuration surface for the SPA
# (additive to /settings/summary, which stays untouched)
# ---------------------------------------------------------------------------

@app.get("/settings")
async def get_full_settings():
    """Full settings snapshot; secrets are masked ("abcd••••wxyz")."""
    from app.services.settings_io import full_settings

    return full_settings()


@app.patch("/settings")
async def patch_settings(body: dict):
    """Persist whitelisted settings to .env (+ live env) and re-cache config.

    Body: {"updates": {"CHAT_MODEL": "...", ...}} (flat {"KEY": val} also accepted).
    Unknown/non-whitelisted keys are rejected with HTTP 400.
    """
    from app.services.settings_io import full_settings, update_env

    updates = None
    if isinstance(body, dict):
        if isinstance(body.get("updates"), dict):
            updates = body["updates"]
        else:
            updates = {k: v for k, v in body.items() if k.isupper() and isinstance(v, (str, int, float, bool))}
    if not updates:
        return JSONResponse(status_code=400, content={"error": "no settings keys supplied"})
    try:
        applied = update_env(updates)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    return {"status": "saved", "applied": applied, "settings": full_settings()}


@app.post("/settings/test-connection")
async def settings_test_connection(body: dict):
    """Test gateway credentials the user has typed but not yet saved."""
    from app.models_openrouter import test_connection

    return test_connection(api_key=body.get("api_key"), base_url=body.get("base_url"))


@app.get("/settings/models")
async def settings_models(api_key: str | None = None, base_url: str | None = None):
    """Model list from the gateway (optionally with unsaved credentials)."""
    from app.models_openrouter import fetch_models

    models = fetch_models(api_key=api_key, base_url=base_url)
    return {"count": len(models), "models": models}


# ---------------------------------------------------------------------------
# Jobs – progress for long-running operations (ingest / podcast / OCR)
# ---------------------------------------------------------------------------

@app.get("/jobs")
async def jobs_list():
    from app.services import jobs

    return {"count": len(jobs.list_jobs()), "jobs": jobs.list_jobs()}


@app.get("/jobs/{job_id}")
async def jobs_get(job_id: str):
    from app.services import jobs

    job = jobs.get_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "job not found"})
    return job


@app.post("/jobs/{job_id}/cancel")
async def jobs_cancel(job_id: str):
    from app.services import jobs

    return {"requested": jobs.request_cancel(job_id)}


# ---------------------------------------------------------------------------
# Activity feed – recent documents & podcast episodes (Dashboard)
# ---------------------------------------------------------------------------

@app.get("/activity")
async def activity(limit: int = 20):
    meta = MetadataStore()
    try:
        docs = meta.list_documents()
        pods = meta.list_podcasts()
    finally:
        meta.close()

    items = []
    for d in docs:
        items.append(
            {
                "kind": "document",
                "title": d.get("filename") or "",
                "detail": d.get("status") or "",
                "pages": d.get("page_count") or 0,
                "project_id": d.get("project_id") or "",
                "when": d.get("created_at") or "",
            }
        )
    for p in pods:
        items.append(
            {
                "kind": "podcast",
                "title": p.get("title") or "",
                "detail": p.get("status") or "",
                "duration": p.get("duration_sec") or 0,
                "tts_provider": p.get("tts_provider") or "",
                "project_id": p.get("project_id") or "",
                "when": p.get("created_at") or "",
            }
        )
    items = [i for i in items if i["when"]]
    items.sort(key=lambda i: i["when"], reverse=True)
    return {"count": len(items[: max(1, min(limit, 100))]), "items": items[: max(1, min(limit, 100))]}


# ---------------------------------------------------------------------------
# Citations export – bibliography text download (APA 7 / Vancouver)
# ---------------------------------------------------------------------------

@app.get("/citations/export")
async def citations_export(style: str = "apa", project_id: str | None = None):
    from app.citations.formatters import format_reference
    from app.citations.service import list_verified_documents

    style = style.lower().strip()
    if style not in ("apa", "vancouver"):
        style = "apa"

    docs = [
        d for d in list_verified_documents()
        if not project_id or d.get("project_id") == project_id
    ]
    # only documents with enough verified metadata to make a real reference
    docs = [d for d in docs if (d.get("title") or d.get("journal"))]
    refs = [format_reference(d, style) for d in docs]
    if style == "vancouver":
        refs = [f"{i}. {r}" for i, r in enumerate(refs, start=1)]
    else:
        refs = sorted(refs, key=str.lower)
    text = "\n\n".join(refs)
    return {
        "style": style,
        "count": len(refs),
        "references": refs,
        "project_id": project_id,
        "text": text,
    }


# ---------------------------------------------------------------------------
# OCR to Markdown (v2 API surface – ported from the Streamlit OCR page)
# ---------------------------------------------------------------------------

@app.post("/ocr/extract")
async def ocr_extract(
    file: UploadFile = File(...),
    backend: str = Form(""),
    force_ocr: bool = Form(False),
    dpi: int = Form(200),
    model: str = Form(""),
):
    """Extract Markdown from one PDF or image as a background job.

    Returns a job id immediately; poll GET /jobs/{id} for progress
    (per page) and the result (markdown + saved file name).
    """
    import threading

    from app.config import get_settings as _gs
    from app.services import jobs
    from app.services.ocr_service import extract_and_save

    cfg = _gs()
    use_backend = (backend or "").strip().lower() or (cfg.ocr_backend if cfg.ocr_backend in
                    ("tesseract", "vision", "paddleocr-vl", "teleocr") else "tesseract")
    use_model = (model or "").strip() or (cfg.chat_model if use_backend == "vision" else "")

    filename = file.filename or "output.pdf"
    data = await file.read()

    job = jobs.create_job(
        kind="ocr",
        label=f"OCR · {filename}",
        payload={"backend": use_backend, "force_ocr": bool(force_ocr), "dpi": int(dpi),
                 "model": use_model or None, "filename": filename, "size": len(data)},
    )

    def _run(job_id=job["id"]):
        from app.services.ocr_service import OCRCancelled

        try:
            jobs.update_job(job_id, status="running", progress=0.0, message=f"Processing {filename}…")
            result = extract_and_save(
                data, filename, backend=use_backend, force_ocr=bool(force_ocr),
                dpi=int(dpi), model=use_model or None,
                progress_cb=lambda p, msg: jobs.update_job(job_id, progress=p, message=msg),
                cancel=jobs.cancel_event(job_id),
            )
            jobs.finish(job_id, result=result)
            logger.info("OCR job %s completed: %s (%d pages)", job_id, result["saved_name"], result["pages"])
        except OCRCancelled:
            logger.info("OCR job %s cancelled", job_id)
            jobs.update_job(job_id, status="cancelled", error="cancelled by user")
        except Exception as exc:  # noqa: BLE001
            logger.exception("OCR job %s failed: %s", job_id, exc)
            jobs.update_job(job_id, status="failed", error=str(exc))

    threading.Thread(target=_run, daemon=True, name=f"ocr-{filename}").start()
    return {"job_id": job["id"], "kind": "ocr", "filename": filename, "backend": use_backend}


@app.get("/ocr/output")
async def ocr_output_list():
    """Saved .md files in data/ocr_output (newest first)."""
    from app.services.ocr_service import list_output

    return {"count": len(list_output()), "files": list_output()}


@app.get("/ocr/output/{name}/content")
async def ocr_output_content(name: str):
    """Markdown text of one saved file (for the Rendered/Raw preview tabs)."""
    from app.services.ocr_service import read_output

    text = read_output(name)
    if text is None:
        return JSONResponse(status_code=404, content={"error": "file not found"})
    return {"name": name, "content": text}


@app.get("/ocr/output/{name}")
async def ocr_output_download(name: str):
    """Download one saved .md file."""
    from fastapi.responses import FileResponse

    from app.services.ocr_service import output_path

    path = output_path(name)
    if not path:
        return JSONResponse(status_code=404, content={"error": "file not found"})
    return FileResponse(path, media_type="text/markdown", filename=name)


@app.delete("/ocr/output/{name}")
async def ocr_output_delete(name: str):
    from app.services.ocr_service import delete_output

    if not delete_output(name):
        return JSONResponse(status_code=404, content={"error": "file not found"})
    return {"deleted": name}


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

@app.get("/logs")
async def get_logs(n: int = 500, level: str = "all"):
    """Return recent log lines.

    Query params:
      n     – number of lines (default 500, max 2000)
      level – filter by level: all | debug | info | warning | error | critical
    """
    n = min(n, 2000)
    lines = get_recent_logs(n)
    level_lower = level.strip().lower()
    if level_lower != "all":
        lines = [line for line in lines if level_lower in line.lower()]
    return {"count": len(lines), "lines": lines}


@app.delete("/logs")
async def delete_logs():
    """Clear in-memory log buffer."""
    clear_logs()
    logger.info("In-memory log buffer cleared")
    return {"status": "cleared"}


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@app.get("/projects")
async def list_projects():
    meta = MetadataStore()
    try:
        return meta.list_projects()
    finally:
        meta.close()


@app.post("/projects")
async def create_project(name: str, description: str = ""):
    meta = MetadataStore()
    try:
        return meta.create_project(name, description)
    finally:
        meta.close()


@app.get("/projects/{project_id}")
async def get_project(project_id: str):
    meta = MetadataStore()
    try:
        proj = meta.get_project(project_id)
        if not proj:
            return JSONResponse(status_code=404, content={"error": "project not found"})
        proj["doc_count"] = len(meta.list_documents(project_id))
        proj["chat_count"] = len(_chat_store().list_sessions(project_id))
        return proj
    finally:
        meta.close()


@app.patch("/projects/{project_id}")
async def update_project(project_id: str, body: dict):
    meta = MetadataStore()
    try:
        fields = {}
        if body.get("name") is not None and str(body.get("name")).strip():
            fields["name"] = str(body.get("name")).strip()
        if body.get("description") is not None:
            fields["description"] = str(body.get("description")).strip()

        # Per-project model/tuning overrides. An explicit empty string / null
        # means "clear the override -> fall back to the global default".
        if "chat_model" in body:                       # set or clear (None/"")
            fields["chat_model"] = str(body.get("chat_model") or "").strip()
        if "chat_temperature" in body:
            raw = body.get("chat_temperature")
            # "" or None -> clear the override (NULL = use global)
            if raw in (None, ""):
                fields["chat_temperature"] = None
            else:
                try:
                    fields["chat_temperature"] = float(raw)
                except (TypeError, ValueError):
                    return JSONResponse(status_code=400, content={"error": "invalid chat_temperature"})
        if "top_k" in body:
            raw = body.get("top_k")
            if raw in (None, ""):
                fields["top_k"] = None
            else:
                try:
                    fields["top_k"] = int(raw)
                except (TypeError, ValueError):
                    return JSONResponse(status_code=400, content={"error": "invalid top_k"})

        if not fields:
            return JSONResponse(status_code=400, content={"error": "no fields to update"})
        updated = meta.update_project(project_id, **fields)
        if not updated:
            return JSONResponse(status_code=404, content={"error": "project not found"})
        return updated
    finally:
        meta.close()


@app.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    """Delete a project and clean up ALL of its data: document metadata,
    vector chunks, chat sessions/messages, and stored uploaded files."""
    meta = MetadataStore()
    vs = VectorStore("default")
    try:
        docs = meta.list_documents(project_id)
        chunks_removed = 0
        for doc in docs:
            chunks_removed += vs.delete_document(doc["id"])
            meta.clear_summaries(doc["id"])
            if doc.get("stored_path"):
                Path(doc["stored_path"]).unlink(missing_ok=True)
        # delete chat sessions belonging to this project
        chat = _chat_store()
        try:
            for sess in chat.list_sessions(project_id):
                chat.delete_session(sess["id"])
        finally:
            chat.close()
        deleted = meta.delete_project(project_id)
        return {
            "deleted": deleted,
            "documents_removed": len(docs),
            "chunks_removed": chunks_removed,
        }
    finally:
        vs.close()
        meta.close()


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@app.get("/documents")
async def list_all_documents(project_id: str | None = None):
    """All documents (across projects, newest first) or filtered by project.

    Used by the v2 SPA Library view; the project-scoped route below stays
    untouched.
    """
    meta = MetadataStore()
    try:
        return meta.list_documents(project_id or None)
    finally:
        meta.close()


@app.get("/projects/{project_id}/documents")
async def list_documents(project_id: str):
    meta = MetadataStore()
    try:
        return meta.list_documents(project_id)
    finally:
        meta.close()


@app.post("/projects/{project_id}/documents/upload")
async def upload_document(project_id: str, file: UploadFile = File(...)):
    """Store an uploaded file and register it. Does NOT ingest yet."""
    content = await file.read()
    from app.ingest.parsers import ParseError

    try:
        doc = store_upload(project_id, content, file.filename or "unnamed")
        return doc
    except ParseError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Upload failed for %s", project_id)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/documents/{document_id}/ingest")
async def ingest_endpoint(document_id: str, reindex: bool = False, ocr: bool | None = None):
    """Run the full parsing/chunking/embedding pipeline for a document.

    `ocr` selects the indexing mode: True = OCR + indexing (scanned pages OCR'd),
    False = direct embedding (text only, fastest for Word/text files),
    omitted/None = use the configured default. Deterministic in that a given
    (ocr, reindex) combination reproduces the exact same chunk set.
    """
    try:
        summary = ingest_document(document_id, reindex=reindex, ocr=ocr)
        return summary
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ingest failed for %s", document_id)
        return JSONResponse(status_code=400, content={"error": str(exc)})


@app.post("/documents/{document_id}/ingest-async")
async def ingest_async(document_id: str, ocr_mode: str = "auto"):
    """(Re)index a document as a background job; poll GET /jobs/{id}.

    `ocr_mode`: auto (configured default) | direct (text only) | ocr (force).
    """
    import threading

    from app.services import jobs

    try:
        ocr = _ocr_flag(ocr_mode)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    meta = MetadataStore()
    try:
        doc = meta.get_document(document_id)
    finally:
        meta.close()
    if not doc:
        return JSONResponse(status_code=404, content={"error": "document not found"})

    job = jobs.create_job(kind="ingest", label=f"Ingest · {doc['filename']}",
                          payload={"document_id": document_id, "ocr_mode": (ocr_mode or "auto"), "reindex": True})
    threading.Thread(target=_run_ingest_job, args=(job["id"], document_id),
                     kwargs={"ocr": ocr, "reindex": True}, daemon=True).start()
    return {"job_id": job["id"], "kind": "ingest", "document": doc}


@app.post("/projects/{project_id}/ingest-async")
async def upload_and_ingest_async(
    project_id: str,
    file: UploadFile = File(...),
    ocr_mode: str = Form("auto"),
):
    """Store an upload and (re)index it in the background as a /jobs item.

    Returns `{job_id, document}` immediately; the document row appears in the
    library right away (status pending → parsing → embedding → indexed) and
    the SPA polls its status live.
    """
    import threading

    from app.services import jobs

    try:
        ocr = _ocr_flag(ocr_mode)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    content = await file.read()
    try:
        doc = store_upload(project_id, content, file.filename or "unnamed")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Upload for ingest failed")
        return JSONResponse(status_code=400, content={"error": str(exc)})

    job = jobs.create_job(kind="ingest", label=f"Ingest · {doc['filename']}",
                          payload={"document_id": doc["id"], "ocr_mode": (ocr_mode or "auto"), "reindex": False})
    threading.Thread(target=_run_ingest_job, args=(job["id"], doc["id"]),
                     kwargs={"ocr": ocr, "reindex": False}, daemon=True).start()
    return {"job_id": job["id"], "kind": "ingest", "document": doc}


@app.post("/projects/{project_id}/upload-and-ingest")
async def upload_and_ingest(project_id: str, file: UploadFile = File(...), ocr: bool | None = None):
    """Store an upload and immediately ingest it with the chosen mode."""
    content = await file.read()
    try:
        doc = store_upload(project_id, content, file.filename or "unnamed")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Upload+ingest failed")
        return JSONResponse(status_code=400, content={"error": str(exc)})
    try:
        summary = ingest_document(doc["id"], ocr=ocr)
        summary["document"] = doc
        return summary
    except Exception as exc:  # noqa: BLE001
        logger.exception("Upload+ingest failed")
        return JSONResponse(status_code=400, content={"error": str(exc), "document": doc})


# ---------------------------------------------------------------------------
# Async ingest (v2 SPA) — background jobs so the UI stays responsive
# ---------------------------------------------------------------------------

_OCR_MODES = ("auto", "direct", "ocr")


def _ocr_flag(ocr_mode: str) -> bool | None:
    """'auto' | 'direct' | 'ocr' -> None | False | True (pipeline convention)."""
    mode = (ocr_mode or "auto").strip().lower()
    if mode == "direct":
        return False
    if mode == "ocr":
        return True
    if mode in _OCR_MODES:
        return None
    raise ValueError(f"ocr_mode must be one of {', '.join(_OCR_MODES)}")


def _run_ingest_job(job_id: str, document_id: str, *, ocr: bool | None, reindex: bool = False) -> None:
    """Worker thread: run the full pipeline as a /jobs item."""
    from app.ingest import pipeline
    from app.services import jobs

    try:
        meta = MetadataStore()
        try:
            doc = meta.get_document(document_id)
        finally:
            meta.close()
        label = doc["filename"] if doc else document_id
        jobs.update_job(job_id, status="running", progress=None, message=f"Indexing {label}…")
        summary = pipeline.ingest_document(document_id, reindex=reindex, ocr=ocr)
        jobs.finish(job_id, result=summary)
        logger.info("Ingest job %s completed for %s (%d chunks)", job_id, label, summary["chunks"])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ingest job %s failed for %s", job_id, document_id)
        jobs.update_job(job_id, status="failed", error=str(exc))


@app.delete("/documents/{document_id}")
async def delete_document(document_id: str):
    """Delete a document's chunks + metadata + stored file."""
    meta = MetadataStore()
    vs = VectorStore("default")
    try:
        doc = meta.get_document(document_id)
        removed_chunks = vs.delete_document(document_id)
        meta.clear_summaries(document_id)
        meta.delete_document(document_id)
        if doc and doc.get("stored_path"):
            Path(doc["stored_path"]).unlink(missing_ok=True)
        return {"deleted": document_id, "chunks_removed": removed_chunks}
    finally:
        vs.close()
        meta.close()


@app.post("/documents/{document_id}/summary")
async def document_summary(document_id: str, kind: str = "brief", force: bool = False):
    """Generate an on-demand summary of one indexed document.

    `kind` is one of: brief | detailed | key_points | tldr | quiz.
    `force=True` regenerates even if a cached summary exists.
    """
    from app.models_openrouter import LLMClientError
    from app.rag.summarize import summarize_document

    try:
        return summarize_document(document_id, kind=kind, force=force)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except LLMClientError as exc:
        logger.exception("Document summary failed for %s", document_id)
        return JSONResponse(status_code=502, content={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Document summary failed for %s", document_id)
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ---------------------------------------------------------------------------
# Tags & notes (Phase 8)
# ---------------------------------------------------------------------------

@app.get("/tags")
async def list_all_tags():
    """All distinct tags across documents, with a document count."""
    meta = MetadataStore()
    try:
        tags = meta.list_tags()
        return [{"tag": t, "documents": len(meta.list_documents_by_tag(t))} for t in tags]
    finally:
        meta.close()


@app.patch("/documents/{document_id}/tags")
async def update_document_tags(document_id: str, body: dict):
    """Set a document's tags (JSON list)."""
    meta = MetadataStore()
    try:
        updated = meta.set_tags(document_id, body.get("tags") or [])
        if not updated:
            return JSONResponse(status_code=404, content={"error": "document not found"})
        return {"tags": meta.get_tags(document_id)}
    finally:
        meta.close()


@app.patch("/documents/{document_id}/notes")
async def update_document_notes(document_id: str, body: dict):
    """Set a document's freeform notes."""
    meta = MetadataStore()
    try:
        updated = meta.set_notes(document_id, body.get("notes") or "")
        if not updated:
            return JSONResponse(status_code=404, content={"error": "document not found"})
        return {"notes": (updated or {}).get("notes", "")}
    finally:
        meta.close()


@app.get("/index/stats")
async def index_stats():
    """Chunk counts and indexed document ids for the vector store."""
    vs = VectorStore("default")
    meta = MetadataStore()
    cfg = get_settings()
    try:
        from app.ingest.embeddings import current_embedding_label

        current_model = current_embedding_label()
        stored_model = vs.get_meta("embedding_model")
        matched = (
            stored_model is None
            or current_model is None
            or stored_model.strip() == (current_model or "").strip()
        )
        return {
            "chunks": vs.count(),
            "indexed_documents": vs.documents(),
            "document_count": len(meta.list_documents()),
            "embedding_dim": vs.embedding_dim(),
            "embedding_model": stored_model,
            "embedding_model_matches": matched,
            "current_embedding_model": current_model,
        }
    finally:
        vs.close()
        meta.close()


# ---------------------------------------------------------------------------
# Citations (Phase 4)
# ---------------------------------------------------------------------------

@app.get("/documents/{document_id}/citations/auto")
async def citations_auto(document_id: str):
    """Best-effort auto-extraction of citation metadata (Crossref)."""
    from app.citations.service import auto_metadata

    try:
        return auto_metadata(document_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Auto citation failed for %s", document_id)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/documents/{document_id}/citations/confirm")
async def citations_confirm(document_id: str, body: dict):
    """Save user-confirmed citation metadata for a document."""
    from app.citations.service import confirm_metadata

    fields = body.get("metadata") or {}
    try:
        return confirm_metadata(document_id, fields)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Confirm citation failed for %s", document_id)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/citations/resolve")
async def citations_resolve(body: dict):
    """Resolve an answer's source list into references for a citation style."""
    from app.citations.resolver import resolve_citations

    style = (body.get("style") or "").strip() or "apa"
    try:
        return resolve_citations(
            body.get("answer_text") or "",
            body.get("sources") or [],
            style=style,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Citation resolve failed")
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ---------------------------------------------------------------------------
# Chat sessions
# ---------------------------------------------------------------------------

@app.get("/chat/sessions")
async def chat_sessions(project_id: str | None = None):
    from app.db.chat import ChatStore

    store = ChatStore()
    try:
        return store.list_sessions(project_id)
    finally:
        store.close()


@app.post("/chat/sessions")
async def create_chat_session(project_id: str = "", title: str = "New chat"):
    from app.db.chat import ChatStore

    store = ChatStore()
    try:
        return store.create_session(project_id, title)
    finally:
        store.close()


@app.get("/chat/sessions/{session_id}/messages")
async def chat_messages(session_id: str):
    from app.db.chat import ChatStore

    store = ChatStore()
    try:
        return store.get_messages(session_id)
    finally:
        store.close()


@app.patch("/chat/sessions/{session_id}")
async def rename_chat_session(session_id: str, body: dict):
    from app.db.chat import ChatStore

    title = (body.get("title") or "").strip()
    if not title:
        return JSONResponse(status_code=400, content={"error": "title required"})
    store = ChatStore()
    try:
        store.rename_session(session_id, title)
        sess = store.get_session(session_id)
        return sess or {"id": session_id, "title": title}
    finally:
        store.close()


@app.delete("/chat/sessions/{session_id}")
async def delete_chat_session(session_id: str):
    from app.db.chat import ChatStore

    store = ChatStore()
    try:
        deleted = store.delete_session(session_id)
        return {"deleted": deleted}
    finally:
        store.close()


@app.post("/chat/ask")
async def chat_ask(body: dict):
    """One grounded chat turn over the corpus."""
    from app.models_openrouter import LLMClientError
    from app.rag.engine import ChatError, ask

    question = (body.get("question") or "").strip()
    if not question:
        return JSONResponse(status_code=400, content={"error": "question required"})
    try:
        result = ask(
            question,
            session_id=body.get("session_id") or None,
            project_id=body.get("project_id") or None,
            top_k=body.get("top_k") or None,
            style=body.get("style") or None,
            distributed=(
                body.get("distributed")
                if body.get("distributed") is not None
                else None
            ),
            temperature=(
                float(body["temperature"])
                if body.get("temperature") is not None
                else None
            ),
            model=body.get("model") or None,
        )
        # serialize sources: enrich each AnswerSource with the resolved chunk info
        chunk_by_key = {}
        for c in result.get("used_chunks", []):
            chunk_by_key[(c["document_id"], c["page"])] = c
        result["sources"] = [
            {
                "document_id": s.document_id,
                "page": s.page,
                "section": (chunk_by_key.get((s.document_id, s.page)) or {}).get("section", ""),
                "text": (chunk_by_key.get((s.document_id, s.page)) or {}).get("text", "")[:400],
                "score": round(float((chunk_by_key.get((s.document_id, s.page)) or {}).get("score", 0.0)), 4),
            }
            for s in result.get("sources", [])
        ]
        return result
    except (ChatError, LLMClientError) as exc:
        logger.exception("Chat ask failed")
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ---------------------------------------------------------------------------
# Podcasts (Phase 8 – NotebookLM-style deep dives)
# ---------------------------------------------------------------------------

@app.get("/podcasts")
async def list_podcasts(project_id: str | None = None):
    meta = MetadataStore()
    try:
        pods = meta.list_podcasts(project_id)
        # enrich with document filenames for display
        seen_docs = {}
        for p in pods:
            names = []
            for did in p.get("document_ids", []):
                if did not in seen_docs:
                    d = meta.get_document(did)
                    seen_docs[did] = d["filename"] if d else did
                names.append(seen_docs[did])
            p["document_filenames"] = names
        return pods
    finally:
        meta.close()


@app.post("/podcasts/generate")
async def create_podcast(body: dict):
    """Generate a podcast (script + optional audio) over the given documents."""
    from app.podcast.generate import PodcastError, generate_podcast

    project_id = body.get("project_id") or ""
    document_ids = body.get("document_ids") or []
    title = (body.get("title") or "").strip() or "Untitled deep dive"
    model = body.get("model") or None
    provider = body.get("provider") or None
    system_prompt = body.get("system_prompt") or None
    duration_setting = body.get("duration_setting") or "medium"
    if not project_id:
        return JSONResponse(status_code=400, content={"error": "project_id required"})
    if not document_ids:
        return JSONResponse(status_code=400, content={"error": "document_ids required"})
    try:
        pod = generate_podcast(
            project_id=project_id, document_ids=document_ids,
            title=title, model=model, provider=provider,
            system_prompt=system_prompt, duration_setting=duration_setting,
        )
        return pod
    except PodcastError as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Podcast generation failed")
        return JSONResponse(status_code=500, content={"error": str(exc)})


@app.get("/podcasts/{podcast_id}")
async def get_podcast(podcast_id: str):
    meta = MetadataStore()
    try:
        pod = meta.get_podcast(podcast_id)
        if not pod:
            return JSONResponse(status_code=404, content={"error": "podcast not found"})
        return pod
    finally:
        meta.close()


@app.post("/podcasts/{podcast_id}/regenerate")
async def regenerate_podcast(
    podcast_id: str, model: str = "", provider: str | None = None,
    system_prompt: str | None = None, duration_setting: str | None = None
):
    """Regenerate a podcast's script (and audio) for its stored documents."""
    from app.podcast.generate import generate_podcast

    meta = MetadataStore()
    try:
        pod = meta.get_podcast(podcast_id)
        if not pod:
            return JSONResponse(status_code=404, content={"error": "podcast not found"})
        # consume the old audio file
        if pod.get("audio_path"):
            Path(pod["audio_path"]).unlink(missing_ok=True)
        # Use stored settings unless overridden
        sp = system_prompt if system_prompt is not None else pod.get("system_prompt")
        ds = duration_setting if duration_setting is not None else pod.get("duration_setting") or "medium"
        new_pod = generate_podcast(
            project_id=pod["project_id"],
            document_ids=pod["document_ids"],
            title=pod["title"] + " (regenerated)",
            model=model or None,
            provider=provider or None,
            system_prompt=sp,
            duration_setting=ds,
        )
        return new_pod
    finally:
        meta.close()


@app.delete("/podcasts/{podcast_id}")
async def delete_podcast(podcast_id: str):
    """Delete a podcast and its audio file."""
    meta = MetadataStore()
    try:
        pod = meta.get_podcast(podcast_id)
        if pod and pod.get("audio_path"):
            Path(pod["audio_path"]).unlink(missing_ok=True)
        deleted = meta.delete_podcast(podcast_id)
        return {"deleted": deleted}
    finally:
        meta.close()


@app.get("/podcasts/{podcast_id}/audio")
async def podcast_audio(podcast_id: str):
    """Serve the generated MP3 audio file."""
    from fastapi.responses import FileResponse

    meta = MetadataStore()
    try:
        pod = meta.get_podcast(podcast_id)
    finally:
        meta.close()
    if not pod or not pod.get("audio_path"):
        return JSONResponse(status_code=404, content={"error": "no audio file"})
    path = Path(pod["audio_path"])
    if not path.exists():
        return JSONResponse(status_code=404, content={"error": "audio file missing"})
    return FileResponse(path, media_type="audio/mpeg", filename=f"{pod['title']}.mp3")


# ---------------------------------------------------------------------------
# Shutdown
# ---------------------------------------------------------------------------

@app.post("/shutdown")
async def shutdown_api():
    """Stop the local API and UI servers. Responds first, then terminates
    the processes in the background so the response can flush to the client."""
    from app.shutdown import shutdown_async

    shutdown_async()  # sleeps briefly so this response returns before dying
    return {"status": "shutting_down", "message": "API and UI servers will stop."}


# ---------------------------------------------------------------------------
# v2.0 SPA – "Research Studio" web UI, served by the same process
# ---------------------------------------------------------------------------

_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
_DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
# NOTE: "/docs" must be mounted BEFORE the root "/" mount — StaticFiles mounts
# never fall through on a missing file, so the root mount would swallow every
# /docs/* path otherwise.
if _DOCS_DIR.is_dir():
    app.mount("/docs", StaticFiles(directory=str(_DOCS_DIR)), name="docs")
if (_WEB_DIR / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="spa")

if not (_WEB_DIR / "index.html").is_file():
    @app.get("/")
    async def spa_placeholder():
        """API-only mode while the SPA files are not built yet (web/)."""
        return {
            "status": "ok",
            "version": app.version,
            "message": "API is ready. The v2.0 SPA (web/) is not built yet – "
                       "UI_PORT (Streamlit legacy) or /docs can be used meanwhile.",
        }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    cfg = get_settings()
    uvicorn.run(
        "app.main:app",
        host=cfg.host,
        port=cfg.port,
        reload=True,
        log_level=cfg.log_level.lower(),
    )
