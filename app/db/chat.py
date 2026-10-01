"""Chat session history backed by SQLite (plain, user-friendly).

Keeps chat sessions and messages so the Chat UI can show conversation
history across reruns and restarts. This is separate from the vector store
and metadata store (which hold documents).
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return f"chat_{uuid.uuid4().hex[:12]}"


class ChatStore:
    def __init__(self, db_path: Path | None = None) -> None:
        cfg = get_settings()
        self.db_path = db_path or cfg.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                title TEXT,
                created_at TEXT,
                updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,           -- user | assistant
                content TEXT,
                sources TEXT,        -- JSON list of source dicts
                created_at TEXT,
                FOREIGN KEY(session_id) REFERENCES chat_sessions(id)
            );
            """
        )
        self._conn.commit()

    # ---------------------------------------------------------------- sessions
    def create_session(self, project_id: str, title: str = "New chat") -> dict:
        sid = _new_id()
        now = _now()
        self._conn.execute(
            "INSERT INTO chat_sessions (id, project_id, title, created_at, updated_at) VALUES (?,?,?,?,?)",
            (sid, project_id, title, now, now),
        )
        self._conn.commit()
        return self.get_session(sid) or {"id": sid}

    def list_sessions(self, project_id: str | None = None) -> list[dict]:
        if project_id:
            rows = self._conn.execute(
                "SELECT * FROM chat_sessions WHERE project_id = ? ORDER BY updated_at DESC", (project_id,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM chat_sessions ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]

    def get_session(self, session_id: str) -> dict | None:
        r = self._conn.execute("SELECT * FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(r) if r else None

    def rename_session(self, session_id: str, title: str) -> None:
        self._conn.execute(
            "UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?", (title, _now(), session_id)
        )
        self._conn.commit()

    def delete_session(self, session_id: str) -> int:
        self._conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        cur = self._conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        self._conn.commit()
        return cur.rowcount

    # ---------------------------------------------------------------- messages
    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        sources: list[dict] | None = None,
    ) -> dict:
        now = _now()
        cur = self._conn.execute(
            "INSERT INTO chat_messages (session_id, role, content, sources, created_at) VALUES (?,?,?,?,?)",
            (session_id, role, content, json.dumps(sources or []), now),
        )
        self._conn.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE id = ?", (now, session_id)
        )
        self._conn.commit()
        return {
            "id": cur.lastrowid,
            "session_id": session_id,
            "role": role,
            "content": content,
            "sources": sources or [],
            "created_at": now,
        }

    def get_messages(self, session_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["sources"] = json.loads(d["sources"] or "[]")
            except json.JSONDecodeError:
                d["sources"] = []
            out.append(d)
        return out

    def history_for_llm(self, session_id: str, max_turns: int = 6) -> list[dict]:
        """Return recent messages in LLM format (user/assistant), excluding
        the raw SRC tags (only the display text)."""
        msgs = self.get_messages(session_id)
        out = []
        for m in msgs[-max_turns:]:
            role = "user" if m["role"] == "user" else "assistant"
            # assistant content is stored clean (tags stripped in generator)
            out.append({"role": role, "content": m["content"]})
        return out

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass
