"""End-to-end document ingestion: parse -> chunk -> embed -> store.

The public entry point is `ingest_document(...)`, which:
1. parses the file into per-page text (with optional OCR of scanned pages)
2. chunks the pages (with page/section metadata)
3. embeds the chunk texts via the configured embedding backend
4. stores chunks in the vector store and updates metadata status row
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

import numpy as np

from app.config import get_settings
from app.db.metadata import MetadataStore
from app.db.vectorstore import VectorStore
from app.ingest.chunking import chunk_pages
from app.ingest.embeddings import embed_texts
from app.ingest.parsers import ParseError, parse_file

logger = logging.getLogger("app")

# Default vector-store namespace (single user, single corpus).
_NS = "default"


def _set_status(meta: MetadataStore, doc_id: str, status: str, error: str | None = None) -> None:
    fields: dict = {"status": status}
    if error is not None:
        fields["error"] = error
        if status == "error":
            logger.error("Document %s failed: %s", doc_id, error)
        else:
            logger.info("Document %s -> %s", doc_id, status)
    else:
        logger.info("Document %s -> %s", doc_id, status)
    meta.update_document(doc_id, **fields)


def ingest_document(document_id: str, *, reindex: bool = False) -> dict:
    """Parse, chunk, embed and store a document's chunks.

    Returns a summary dict with counts and status.
    """
    cfg = get_settings()
    meta = MetadataStore()
    vs = VectorStore(_NS)

    try:
        doc = meta.get_document(document_id)
        if not doc:
            raise ValueError(f"Document {document_id} not found.")
        stored_path = Path(doc["stored_path"])
        if not stored_path.exists():
            raise FileNotFoundError(f"Stored file missing: {stored_path}")

        # Clean up any previous chunks on reindex
        if reindex:
            vs.delete_document(document_id)

        _set_status(meta, document_id, "parsing")
        pages, file_type = parse_file(stored_path)

        _set_status(meta, document_id, "chunking", None)
        chunks = chunk_pages(pages)

        if not chunks:
            raise ParseError("No text could be extracted/chunked from this document.")

        _set_status(meta, document_id, "embedding", None)
        texts = [c.text for c in chunks]
        vectors = embed_texts(texts)
        if not vectors:
            raise RuntimeError("Embedding backend produced no vectors.")

        _set_status(meta, document_id, "indexing", None)
        embeddings = np.asarray(vectors, dtype=np.float32)
        metadata_rows = [
            {
                "project_id": doc["project_id"],
                "document_id": document_id,
                "source_page": c.page,
                "section": c.section,
                "offset": c.offset,
            }
            for c in chunks
        ]
        try:
            vs.add_chunks(texts, embeddings, metadata_rows)
        except Exception as exc:
            from app.db.vectorstore import DimensionMismatchError  # local import: cheap

            if isinstance(exc, DimensionMismatchError):
                # friendly message for the UI/log instead of a numpy-shape trace
                raise RuntimeError(
                    str(exc) + " Open Settings to change back, or re-index ALL "
                    "documents in this namespace with the new model."
                ) from exc
            raise
        # remember which embedding backend/model produced these vectors
        try:
            vs.set_meta("embedding_model", cfg.embedding_model or cfg.local_embedding_model)
            vs.set_meta("embedding_backend", cfg.embedding_backend)
            vs.set_meta("embedding_dim", str(embeddings.shape[1]))
        except Exception:  # noqa: BLE001
            logger.debug("Failed to record embedding identity", exc_info=True)

        # update doc: page count, status, file type
        meta.update_document(
            document_id,
            page_count=max((p["page"] for p in pages), default=0) + 1,
            file_type=file_type,
            status="indexed",
            error=None,
        )

        summary = {
            "document_id": document_id,
            "filename": doc["filename"],
            "chunks": len(chunks),
            "pages": len(pages),
            "embedding_dim": embeddings.shape[1],
            "status": "indexed",
        }
        logger.info("Ingested %s: %d chunks across %d pages", doc["filename"], len(chunks), len(pages))
        return summary
    except Exception as exc:  # noqa: BLE001
        _set_status(meta, document_id, "error", str(exc))
        raise
    finally:
        vs.close()
        meta.close()


def store_upload(project_id: str, upload_bytes: bytes, filename: str) -> dict:
    """Save an uploaded file to disk and create its metadata row.

    Returns the created document dict.
    """
    cfg = get_settings()
    meta = MetadataStore()
    try:
        name = Path(filename).name
        suffix = Path(name).suffix.lower()
        if suffix not in (".pdf", ".docx", ".doc", ".txt", ".md", ".markdown", ".text"):
            raise ParseError(f"Unsupported file type: {suffix or '(none)'}. Use PDF, DOCX, TXT or Markdown.")
        # unique stored filename
        import uuid

        stored_name = f"{uuid.uuid4().hex[:10]}_{name}"
        dest = cfg.uploads_dir / stored_name
        dest.write_bytes(upload_bytes)

        if suffix == ".pdf":
            ftype = "pdf"
        elif suffix in (".docx", ".doc"):
            ftype = "docx"
        else:
            ftype = "txt"

        doc = meta.create_document(
            project_id=project_id,
            filename=name,
            stored_path=str(dest),
            file_type=ftype,
            status="pending",
        )
        logger.info("Stored upload %s -> %s", name, dest.name)
        return doc
    finally:
        meta.close()
