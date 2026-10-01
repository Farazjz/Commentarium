"""Centralised logging setup.

- console output (always)
- rotating file in data/logs/  (auto-rotates at 5 MB, keeps 5 files)
- in-process log buffer (RingBuffer) that the Streamlit UI reads for the
  Live Log viewer
"""
from __future__ import annotations

import collections
import logging
import logging.handlers
import sys
from typing import Deque

from app.config import get_settings

# ---------------------------------------------------------------------------
# In-process ring buffer – the UI polls this to display recent logs
# ---------------------------------------------------------------------------
_BUFFER_MAX = 2000  # store last 2 000 lines in memory
_log_buffer: Deque[str] = collections.deque(maxlen=_BUFFER_MAX)


class RingBufferHandler(logging.Handler):
    """Streams formatted log lines into a shared deque for the UI."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            _log_buffer.append(msg)
        except Exception:
            self.handleError(record)


def get_recent_logs(n: int = 500) -> list[str]:
    """Return the last *n* log lines from the in-memory buffer."""
    return list(_log_buffer)[-n:]


def clear_logs() -> None:
    _log_buffer.clear()


# ---------------------------------------------------------------------------
# Public setup function
# ---------------------------------------------------------------------------
_LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s:%(lineno)d | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_level: str | None = None) -> None:
    """Configure the root logger.

    Safe to call multiple times (subsequent calls are no-ops beyond
    updating the log level).
    """
    cfg = get_settings()
    level = getattr(logging, (log_level or cfg.log_level).upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Avoid duplicate handlers on repeated calls
    if getattr(root, "_thesis_rag_configured", False):
        root.setLevel(level)
        return

    # ---- formatter --------------------------------------------------------
    fmt = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # ---- console handler (stdout) -----------------------------------------
    console = logging.StreamHandler(stream=sys.stdout)
    console.setLevel(level)
    console.setFormatter(fmt)
    root.addHandler(console)

    # ---- rotating file handler --------------------------------------------
    log_file = cfg.logs_dir / "app.log"
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,   # 5 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # ---- in-memory ring buffer (for UI live-log viewer) -------------------
    ring_handler = RingBufferHandler()
    ring_handler.setLevel(level)
    ring_handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    root.addHandler(ring_handler)

    root._thesis_rag_configured = True  # type: ignore[attr-defined]

    logging.getLogger("app").info("Logging initialised  level=%s  file=%s", level, log_file)
