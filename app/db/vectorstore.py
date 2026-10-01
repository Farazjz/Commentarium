"""Lightweight local vector store built on NumPy + SQLite.

Why not ChromaDB? On Windows + Python 3.12, ChromaDB pulls in `chroma-hnswlib`
which must be compiled from source (needs Visual C++ Build Tools). For a
single-user local app at the 100–300 file scale, a direct cosine-similarity
search over a NumPy matrix is simpler, dependency-free, fully private, and
more than fast enough.

Design
------
- `chunks` table (SQLite): id, project_id, document_id, text, source_page,
  section, offset -> the retrievable text + citation metadata.
- `vectors.npy` (NumPy): a (n_chunks, dim) float32 matrix, row i == chunk id.
- Search = normalized dot product (cosine) over the matrix, top-k by score.
- Row/table ordering is kept in sync via a monotonically increasing id.

This is intentionally simple and synchronous; the ingestion pipeline writes
batches and commits atomically.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

from app.config import get_settings

logger = logging.getLogger("app")


class DimensionMismatchError(Exception):
    pass


class VectorStore:
    def __init__(self, namespace: str = "default") -> None:
        cfg = get_settings()
        self.vector_dir = cfg.vectorstore_dir
        self.vector_dir.mkdir(parents=True, exist_ok=True)
        self.namespace = namespace
        self.db_path = self.vector_dir / f"{namespace}.db"
        # NOTE: np.savez appends ".npz" to the path, so name it .npz explicitly
        # to keep save/load paths consistent (savez would otherwise create a
        # "<name>.npy.npz" that the loader can't find).
        self.vectors_path = self.vector_dir / f"{namespace}_vectors.npz"

        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT,
                document_id TEXT,
                text TEXT NOT NULL,
                source_page INTEGER,
                section TEXT,
                offset INTEGER
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        self._conn.commit()

        # Load existing vectors (column "id" auto-indexes with row order)
        self._ids: np.ndarray | None = None
        self._vectors: np.ndarray | None = None
        if self.vectors_path.exists():
            self._load_vectors()

    # ------------------------------------------------------------------ storage
    def _load_vectors(self) -> None:
        try:
            arr = np.load(self.vectors_path)
            self._ids = arr["ids"]
            self._vectors = arr["vectors"]
        except Exception:  # noqa: BLE001
            logger.exception("Failed to load vectors; starting empty")
            self._ids = None
            self._vectors = None

    def _save_vectors(self) -> None:
        np.savez(
            self.vectors_path,
            ids=self._ids,
            vectors=self._vectors,
        )
        logger.debug(
            "Saved %d vectors to %s",
            len(self._vectors) if self._vectors is not None else 0,
            self.vectors_path,
        )

    def add_chunks(
        self,
        texts: list[str],
        embeddings: np.ndarray,
        metadata: list[dict[str, Any]],
    ) -> list[int]:
        """Insert chunks + embeddings atomically. Returns new row ids."""
        if len(texts) != len(embeddings) != len(metadata):
            raise ValueError("texts/embeddings/metadata length mismatch")

        embeddings = np.asarray(embeddings, dtype=np.float32)
        if embeddings.ndim == 1:
            embeddings = embeddings.reshape(1, -1)

        # Guard against mixing embedding models (silently corrupts search).
        self.check_dimension(embeddings.shape[1])

        ids: list[int] = []
        try:
            for text, meta in zip(texts, metadata):
                cur = self._conn.execute(
                    """
                    INSERT INTO chunks (project_id, document_id, text, source_page, section, offset)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        meta.get("project_id"),
                        meta.get("document_id"),
                        text,
                        meta.get("source_page"),
                        meta.get("section"),
                        meta.get("offset"),
                    ),
                )
                ids.append(cur.lastrowid)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            logger.exception("Failed to insert chunks")
            raise

        # append embeddings to in-memory matrix and persist
        ids_arr = np.asarray(ids, dtype=np.int64).reshape(-1, 1)
        if self._vectors is None or len(self._vectors) == 0:
            self._ids = ids_arr
            self._vectors = embeddings
        else:
            self._ids = np.vstack([self._ids, ids_arr])
            self._vectors = np.vstack([self._vectors, embeddings])
        self._save_vectors()

        logger.info("Indexed %d chunks in namespace %r", len(ids), self.namespace)
        return ids

    # ------------------------------------------------------------------ query
    def _scored_candidates(
        self,
        query_embedding: np.ndarray,
        limit: int,
        *,
        project_id: str | None = None,
    ) -> list[dict]:
        """Return the top-`limit` scored chunk records (project-filtered)."""
        if self._vectors is None or len(self._vectors) == 0:
            return []
        q = np.asarray(query_embedding, dtype=np.float32).reshape(1, -1)
        # A mismatched query dim means the embedding model changed; fail loudly
        # instead of returning garbage similarities.
        self.check_dimension(q.shape[1])
        qn = q / (np.linalg.norm(q) + 1e-9)
        norms = np.linalg.norm(self._vectors, axis=1, keepdims=True) + 1e-9
        sim = (self._vectors / norms) @ qn.T
        scores = sim.flatten()

        order = np.argsort(-scores)
        order = order[: max(limit, 1) * 6]  # oversample, then refine via metadata

        hits = []
        for row_idx in order:
            row_id = int(self._ids[row_idx][0])
            meta_row = self._conn.execute(
                "SELECT * FROM chunks WHERE id = ?", (row_id,)
            ).fetchone()
            if meta_row is None:
                continue
            if project_id is not None and meta_row[1] != project_id:
                continue
            hits.append(
                {
                    "chunk_id": meta_row[0],
                    "project_id": meta_row[1],
                    "document_id": meta_row[2],
                    "text": meta_row[3],
                    "source_page": meta_row[4],
                    "section": meta_row[5],
                    "offset": meta_row[6],
                    "score": float(scores[row_idx]),
                }
            )
            if len(hits) >= limit:
                break
        return hits

    def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 8,
        project_id: str | None = None,
    ) -> list[dict]:
        """Return top-k chunks as dicts with text + citation metadata + score."""
        return self._scored_candidates(query_embedding, top_k, project_id=project_id)

    def search_distributed(
        self,
        query_embedding: np.ndarray,
        *,
        per_doc: int,
        project_id: str | None = None,
        max_docs: int | None = None,
    ) -> list[dict]:
        """Top-`per_doc` chunks per document so every paper contributes.

        Used for overview/comparison questions ("which articles / what are they
        about") where a single global top-k would let one document dominate or
        leave others under-represented.
        """
        # Pull a broad candidate pool, then keep the best `per_doc` per document.
        pool = self._scored_candidates(query_embedding, limit=per_doc * 60, project_id=project_id)
        by_doc: dict[str, list[dict]] = {}
        for rec in pool:
            by_doc.setdefault(rec["document_id"], []).append(rec)
        # order docs by their best chunk score so the strongest docs come first
        doc_order = sorted(
            by_doc.items(),
            key=lambda kv: -max(c["score"] for c in kv[1]),
        )
        if max_docs:
            doc_order = doc_order[:max_docs]
        out: list[dict] = []
        for _doc_id, chunk_list in doc_order:
            out.extend(chunk_list[:per_doc])
        out.sort(key=lambda c: -c["score"])
        return out

    # ------------------------------------------------------------------ counts
    def count(self, document_id: str | None = None) -> int:
        if document_id is None:
            return self._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return self._conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE document_id = ?", (document_id,)
        ).fetchone()[0]

    def documents(self) -> list[str]:
        rows = self._conn.execute("SELECT DISTINCT document_id FROM chunks").fetchall()
        return [r[0] for r in rows if r[0]]

    def chunks_for_document(
        self,
        document_id: str,
        *,
        max_chars: int | None = None,
        prefer_sections: bool = True,
    ) -> list[dict]:
        """Return a document's chunks ordered by (preferred section, offset).

        Used for per-document summaries: pull the substantive sections first
        (Abstract/Intro/Methods/Results), then the rest, bounded by max_chars.
        """
        rows = self._conn.execute(
            "SELECT id, document_id, text, source_page, section, offset "
            "FROM chunks WHERE document_id = ?",
            (document_id,),
        ).fetchall()
        recs = [
            {
                "chunk_id": r[0],
                "document_id": r[1],
                "text": r[2],
                "source_page": r[3],
                "section": r[4] or "",
                "offset": r[5] or 0,
            }
            for r in rows
        ]
        if prefer_sections:
            _pref = {
                "abstract": 0, "introduction": 1, "methods": 2,
                "materials and methods": 2, "results": 3, "discussion": 4,
                "conclusion": 5,
            }
            recs.sort(key=lambda c: (_pref.get((c["section"] or "").strip().lower(), 9), c["offset"]))
        if max_chars:
            acc, out = 0, []
            for c in recs:
                n = len(c["text"])
                if acc + n > max_chars and out:
                    break
                out.append(c)
                acc += n
            recs = out
        return recs

    # ------------------------------------------------------------------ delete
    def delete_document(self, document_id: str) -> int:
        """Delete a document's chunks from both the table and the vector matrix."""
        rows = self._conn.execute(
            "SELECT id FROM chunks WHERE document_id = ?", (document_id,)
        ).fetchall()
        ids = {r[0] for r in rows}
        cur = self._conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
        self._conn.commit()
        removed = cur.rowcount

        if ids and self._vectors is not None:
            keep = [i for i in range(len(self._ids)) if int(self._ids[i][0]) not in ids]
            if keep:
                self._ids = self._ids[keep]
                self._vectors = self._vectors[keep]
                self._save_vectors()
            else:
                self._ids = None
                self._vectors = None
                self.vectors_path.unlink(missing_ok=True)

        logger.info("Deleted %d chunks for document %r", removed, document_id)
        return removed

    def reset(self) -> None:
        """Wipe all chunks and vectors for this namespace."""
        self._conn.execute("DELETE FROM chunks")
        self._conn.execute("DELETE FROM meta")
        self._conn.commit()
        self._ids = None
        self._vectors = None
        self.vectors_path.unlink(missing_ok=True)
        logger.info("Vector store %r reset", self.namespace)

    # ------------------------------------------------------------------ meta
    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------ dims
    def embedding_dim(self) -> int | None:
        """Dimensionality of the stored vectors, or None when empty."""
        if self._vectors is not None and len(self._vectors) > 0:
            return int(self._vectors.shape[1])
        return None

    def check_dimension(self, dim: int) -> None:
        """Raise if storing/querying `dim` would conflict with existing vectors."""
        existing = self.embedding_dim()
        if existing is None:
            return
        if int(dim) != existing:
            raise DimensionMismatchError(
                f"Embedding dimension mismatch: corpus is {existing}-dim but this "
                f"operation is {int(dim)}-dim. You changed embedding models after "
                f"indexing. Re-index this project with the new model or switch back. "
                f"(Tried to store/query {int(dim)}-dim vectors.)"
            )

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass
