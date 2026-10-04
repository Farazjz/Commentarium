"""SQLite metadata store for projects, documents, and their citation metadata.

This complements the vector store (which holds chunk text + embeddings). The
metadata store owns the "document" identity: file location, per-file status,
and the verified citation metadata (authors, year, journal, DOI, style) that
the citation layer will later render.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger("app")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class MetadataStore:
    def __init__(self, db_path: Path | None = None) -> None:
        cfg = get_settings()
        self.db_path = db_path or cfg.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                -- per-project model/tuning overrides (empty = use global defaults)
                chat_model TEXT DEFAULT '',
                chat_temperature REAL DEFAULT 0.2,
                top_k INTEGER DEFAULT 8,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                filename TEXT NOT NULL,
                stored_path TEXT,
                file_type TEXT,           -- pdf | docx
                page_count INTEGER,
                status TEXT,              -- pending | parsing | ocr | embedding | indexed | error
                error TEXT,
                citation_style TEXT,      -- apa | vancouver
                -- citation metadata (verified by the user later; Phase 4 uses it)
                title TEXT,
                authors TEXT,             -- JSON list
                year INTEGER,
                journal TEXT,
                doi TEXT,
                volume TEXT,
                issue TEXT,
                pages TEXT,
                url TEXT,
                citation_key TEXT,
                citation_verified INTEGER DEFAULT 0,   -- 0 = auto/pending, 1 = verified by user
                -- community/organisation metadata (Phase 8)
                tags TEXT DEFAULT '[]',   -- JSON list of freeform tags
                notes TEXT DEFAULT '',    -- freeform user notes
                index_mode TEXT,          -- 'ocr' | 'direct' (mode used for the LAST index; NULL for pending)
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY(project_id) REFERENCES projects(id)
            );

            CREATE TABLE IF NOT EXISTS document_summaries (
                document_id TEXT,
                kind TEXT,                -- brief | detailed | key_points | tldr | quiz
                summary TEXT,
                chunks_used INTEGER,
                pages TEXT,               -- JSON list
                created_at TEXT,
                updated_at TEXT,
                PRIMARY KEY (document_id, kind)
            );

            CREATE TABLE IF NOT EXISTS podcasts (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                title TEXT,
                document_ids TEXT,        -- JSON list
                status TEXT,              -- pending | generating | ready | error
                error TEXT,
                script TEXT,
                audio_path TEXT,
                duration_sec REAL DEFAULT 0,
                tts_provider TEXT DEFAULT '',
                system_prompt TEXT,       -- custom system prompt (NULL = use default)
                duration_setting TEXT,    -- 'short' | 'medium' | 'long'
                created_at TEXT,
                updated_at TEXT
            );
            """
        )
        self._conn.commit()

        # Column migrations for DBs created before these fields existed.
        for col, coltype in (
            ("volume", "TEXT"),
            ("issue", "TEXT"),
            ("pages", "TEXT"),
            ("url", "TEXT"),
            ("citation_verified", "INTEGER DEFAULT 0"),
            ("tags", "TEXT DEFAULT '[]'"),
            ("notes", "TEXT DEFAULT ''"),
            ("index_mode", "TEXT"),
        ):
            try:
                cur = self._conn.execute("PRAGMA table_info(documents)")
                existing = {r[1] for r in cur.fetchall()}
                if col not in existing:
                    self._conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {coltype}")
                    self._conn.commit()
            except Exception:  # noqa: BLE001
                logger.debug("metadata migration skipped for column %s", col)

        # Column migrations for the projects table.
        for col, coltype in (
            ("chat_model", "TEXT DEFAULT ''"),
            ("chat_temperature", "REAL DEFAULT 0.2"),
            ("top_k", "INTEGER DEFAULT 8"),
        ):
            try:
                cur = self._conn.execute("PRAGMA table_info(projects)")
                existing = {r[1] for r in cur.fetchall()}
                if col not in existing:
                    self._conn.execute(f"ALTER TABLE projects ADD COLUMN {col} {coltype}")
                    self._conn.commit()
            except Exception:  # noqa: BLE001
                logger.debug("metadata migration skipped for project column %s", col)

        # Column migrations for the podcasts table.
        for col, coltype in (
            ("tts_provider", "TEXT DEFAULT ''"),
            ("system_prompt", "TEXT"),
            ("duration_setting", "TEXT"),
        ):
            try:
                cur = self._conn.execute("PRAGMA table_info(podcasts)")
                existing = {r[1] for r in cur.fetchall()}
                if col not in existing:
                    self._conn.execute(f"ALTER TABLE podcasts ADD COLUMN {col} {coltype}")
                    self._conn.commit()
            except Exception:  # noqa: BLE001
                logger.debug("metadata migration skipped for podcast column %s", col)

    # ------------------------------------------------------------------ helpers
    def _row(self, table: str, row_id: str) -> dict | None:
        cur = self._conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,))
        r = cur.fetchone()
        return dict(r) if r else None

    # ------------------------------------------------------------------ projects
    def create_project(self, name: str, description: str = "") -> dict:
        pid = _new_id("proj")
        now = _now()
        self._conn.execute(
            "INSERT INTO projects (id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
            (pid, name, description, now, now),
        )
        self._conn.commit()
        return self._row("projects", pid) or {"id": pid}

    def list_projects(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM documents d WHERE d.project_id=p.id) as doc_count "
            "FROM projects p ORDER BY p.updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_project(self, project_id: str) -> dict | None:
        return self._row("projects", project_id)

    def update_project(self, project_id: str, **fields: Any) -> dict | None:
        if not fields:
            return self.get_project(project_id)
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._conn.execute(f"UPDATE projects SET {cols} WHERE id = ?", (*fields.values(), project_id))
        self._conn.commit()
        return self.get_project(project_id)

    def delete_project(self, project_id: str) -> int:
        self._conn.execute("DELETE FROM documents WHERE project_id = ?", (project_id,))
        cur = self._conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        self._conn.commit()
        return cur.rowcount

    # ------------------------------------------------------------------ documents
    def create_document(
        self,
        project_id: str,
        filename: str,
        stored_path: str,
        file_type: str,
        status: str = "pending",
    ) -> dict:
        did = _new_id("doc")
        now = _now()
        self._conn.execute(
            "INSERT INTO documents (id, project_id, filename, stored_path, file_type, status, citation_style, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (did, project_id, filename, stored_path, file_type, status, "apa", now, now),
        )
        self._conn.commit()
        if project_id:
            self.update_project(project_id)
        return self._row("documents", did) or {"id": did}

    def list_documents(self, project_id: str | None = None) -> list[dict]:
        if project_id:
            rows = self._conn.execute(
                "SELECT * FROM documents WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def get_document(self, document_id: str) -> dict | None:
        return self._row("documents", document_id)

    def update_document(self, document_id: str, **fields: Any) -> dict | None:
        if not fields:
            return self.get_document(document_id)
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._conn.execute(f"UPDATE documents SET {cols} WHERE id = ?", (*fields.values(), document_id))
        self._conn.commit()
        return self.get_document(document_id)

    def delete_document(self, document_id: str) -> int:
        cur = self._conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
        self._conn.commit()
        return cur.rowcount

    def set_citation_metadata(
        self,
        document_id: str,
        *,
        verified: bool = False,
        **fields: Any,
    ) -> dict | None:
        """Write citation fields from a canonical metadata dict.

        Accepts title, authors (list or JSON string), year, journal, doi,
        volume, issue, pages, url, citation_key, citation_style.
        `verified=True` marks the metadata as user-confirmed.
        """
        payload: dict[str, Any] = {}
        allowed = {
            "title", "authors", "year", "journal", "doi", "volume",
            "issue", "pages", "url", "citation_key", "citation_style",
        }
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "authors" and isinstance(v, (list, tuple)):
                v = json.dumps(list(v), ensure_ascii=False)
            if k == "year" and isinstance(v, str) and v.strip().isdigit():
                v = int(v.strip())
            payload[k] = v
        if verified:
            payload["citation_verified"] = 1
        return self.update_document(document_id, **payload)

    # ------------------------------------------------------------------ tags / notes
    def get_tags(self, document_id: str) -> list[str]:
        doc = self.get_document(document_id)
        if not doc:
            return []
        raw = doc.get("tags") or "[]"
        try:
            return json.loads(raw) if isinstance(raw, str) else list(raw)
        except json.JSONDecodeError:
            return []

    def set_tags(self, document_id: str, tags: list[str]) -> dict | None:
        cleaned = [str(t).strip() for t in (tags or []) if str(t).strip()]
        return self.update_document(
            document_id, tags=json.dumps(cleaned, ensure_ascii=False)
        )

    def set_notes(self, document_id: str, notes: str) -> dict | None:
        return self.update_document(document_id, notes=(notes or ""))

    def list_tags(self) -> list[str]:
        """All distinct tags across every document, sorted by frequency."""
        rows = self._conn.execute("SELECT tags FROM documents").fetchall()
        counts: dict[str, int] = {}
        for r in rows:
            try:
                taglist = json.loads(r[0] or "[]")
            except json.JSONDecodeError:
                continue
            for t in taglist:
                t = str(t).strip()
                if t:
                    counts[t] = counts.get(t, 0) + 1
        return sorted(counts, key=lambda t: (-counts[t], t))

    def list_documents_by_tag(self, tag: str) -> list[dict]:
        """Documents that contain a given tag."""
        out = []
        for d in self.list_documents():
            if tag in self.get_tags(d["id"]):
                out.append(d)
        return out

    # ------------------------------------------------------------------ summaries
    def save_summary(
        self,
        document_id: str,
        kind: str,
        summary: str,
        *,
        chunks_used: int = 0,
        pages: list[int] | None = None,
    ) -> None:
        now = _now()
        self._conn.execute(
            "INSERT INTO document_summaries (document_id, kind, summary, chunks_used, pages, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(document_id, kind) DO UPDATE SET "
            "summary=excluded.summary, chunks_used=excluded.chunks_used, "
            "pages=excluded.pages, updated_at=excluded.updated_at",
            (
                document_id,
                kind,
                summary,
                chunks_used,
                json.dumps(pages or []),
                now,
                now,
            ),
        )
        self._conn.commit()

    def get_summary(self, document_id: str, kind: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM document_summaries WHERE document_id=? AND kind=?",
            (document_id, kind),
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["pages"] = json.loads(d["pages"] or "[]")
        except json.JSONDecodeError:
            d["pages"] = []
        return d

    def list_summary_kinds(self, document_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT kind FROM document_summaries WHERE document_id=?",
            (document_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def clear_summaries(self, document_id: str) -> None:
        self._conn.execute(
            "DELETE FROM document_summaries WHERE document_id=?", (document_id,)
        )
        self._conn.commit()

    # ------------------------------------------------------------------ podcasts
    def create_podcast(
        self,
        *,
        project_id: str,
        title: str,
        document_ids: list[str],
        status: str = "pending",
    ) -> dict:
        pid = _new_id("pod")
        now = _now()
        self._conn.execute(
            "INSERT INTO podcasts (id, project_id, title, document_ids, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (pid, project_id, title, json.dumps(document_ids or []), status, now, now),
        )
        self._conn.commit()
        return self.get_podcast(pid) or {"id": pid}

    def get_podcast(self, podcast_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM podcasts WHERE id=?", (podcast_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["document_ids"] = json.loads(d["document_ids"] or "[]")
        except json.JSONDecodeError:
            d["document_ids"] = []
        return d

    def list_podcasts(self, project_id: str | None = None) -> list[dict]:
        if project_id:
            rows = self._conn.execute(
                "SELECT * FROM podcasts WHERE project_id=? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM podcasts ORDER BY created_at DESC"
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["document_ids"] = json.loads(d["document_ids"] or "[]")
            except json.JSONDecodeError:
                d["document_ids"] = []
            out.append(d)
        return out

    def update_podcast(self, podcast_id: str, **fields: Any) -> dict | None:
        if not fields:
            return self.get_podcast(podcast_id)
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._conn.execute(
            f"UPDATE podcasts SET {cols} WHERE id = ?", (*fields.values(), podcast_id)
        )
        self._conn.commit()
        return self.get_podcast(podcast_id)

    def delete_podcast(self, podcast_id: str) -> int:
        cur = self._conn.execute("DELETE FROM podcasts WHERE id = ?", (podcast_id,))
        self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass
