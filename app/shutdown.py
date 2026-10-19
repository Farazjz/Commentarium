"""Shutdown helper: gracefully stop the local API and UI servers.

The app runs two local processes that the user starts from a terminal:
  1. FastAPI backend (uvicorn) on the API port (default 8000).
  2. Streamlit UI on the UI port (default 8501).

This uses `psutil` to find whichever process is listening on each port and
terminates it, so the "Exit" button can shut everything down from inside the
web UI without needing to touch the terminal.

Either of the two servers may run this helper (the Streamlit UI calls it
directly; the API can call it via the /shutdown endpoint). Whichever process
invokes it first kills the *other* server, then terminates itself with
``os._exit`` so the shutdown always completes regardless of which server you
trigger it from.
"""
from __future__ import annotations

import logging
import os
import threading
import time

import psutil

logger = logging.getLogger("app")

# Default ports (overridden at runtime from app config - see _resolve_ports).
_API_PORT_DEFAULT = 8000
_UI_PORT_DEFAULT = 8501

# How long to wait for a process to die before forcing it.
_GRACE_SECONDS = 6.0

# Process names we are ever allowed to terminate (avoids killing unrelated
# software that happens to hold the port). pythonw.exe matters because v2
# launches the API headless via _run_api.py under pythonw.
_ALLOWED_PROC_NAMES = {
    "python", "python.exe", "pythonw", "pythonw.exe",
    "streamlit", "uvicorn",
}


def _resolve_ports(api_port: int | None, ui_port: int | None) -> tuple[int, int]:
    """Actual ports: explicit arguments first, live config second, constants last."""
    if api_port is not None and ui_port is not None:
        return api_port, ui_port
    try:
        from app.config import get_settings

        cfg = get_settings()
        if api_port is None:
            api_port = getattr(cfg, "port", None) or _API_PORT_DEFAULT
        if ui_port is None:
            ui_port = getattr(cfg, "ui_port", None) or _UI_PORT_DEFAULT
    except Exception:  # noqa: BLE001 - config unavailable -> historical defaults
        pass
    return api_port or _API_PORT_DEFAULT, ui_port or _UI_PORT_DEFAULT


def _pid_on_port(port: int) -> int | None:
    """Return the PID of the process listening on `port`, or None."""
    for conn in psutil.net_connections(kind="tcp"):
        if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
            return conn.pid
    return None


def _kill_port(port: int, self_pid: int) -> dict:
    """Terminate the process listening on `port`.

    If the listener is the calling process itself, we cannot psutil-kill it,
    so we return ``stopped=False`` with reason ``self`` and the caller is
    responsible for exiting (see :func:`shutdown_servers`).
    """
    pid = _pid_on_port(port)
    if pid is None:
        return {"port": port, "pid": None, "stopped": False, "reason": "not listening"}
    if pid == self_pid:
        return {"port": port, "pid": pid, "stopped": False, "reason": "self"}

    try:
        proc = psutil.Process(pid)
        # Never kill unrelated processes (e.g. the SSH/system shell).
        if proc.name().lower() not in _ALLOWED_PROC_NAMES:
            return {"port": port, "pid": pid, "stopped": False,
                    "reason": f"unexpected proc ({proc.name()})"}
    except psutil.NoSuchProcess:
        return {"port": port, "pid": pid, "stopped": False, "reason": "already gone"}

    try:
        proc.terminate()
        proc.wait(timeout=_GRACE_SECONDS)
    except psutil.TimeoutExpired:
        proc.kill()
    except psutil.NoSuchProcess:
        pass

    return {"port": port, "pid": pid, "stopped": True, "reason": "terminated"}


def shutdown_servers(api_port: int | None = None, ui_port: int | None = None) -> list[dict]:
    """Stop the processes on the API and UI ports.

    Ports resolve to explicit arguments, else the live `PORT` / `UI_PORT`
    configuration (so the v2 instance on :8010 kills *itself*, and the
    legacy Streamlit UI on UI_PORT as well).
    Kills any *other* listening server, then — if the calling process is itself
    one of the servers (e.g. running inside Streamlit) — terminates itself last
    with ``os._exit`` so the exit always completes. Returns a list of per-port
    result summaries (mainly useful when called from a third party).
    """
    api_port, ui_port = _resolve_ports(api_port, ui_port)
    self_pid = psutil.Process().pid
    results = []
    self_seen = False
    for port in (api_port, ui_port):
        try:
            res = _kill_port(port, self_pid)
            if res["reason"] == "self":
                self_seen = True
                res = {**res, "stopped": True, "reason": "self-exit"}
            results.append(res)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed to stop server on port %s", port)
            results.append({"port": port, "pid": None, "stopped": False,
                            "reason": f"error: {exc}"})

    if self_seen:
        # Give the success message a moment to reach the browser before dying.
        time.sleep(0.4)
        os._exit(0)  # noqa: PLR1722
    return results


def shutdown_async(api_port: int | None = None, ui_port: int | None = None) -> None:
    """Fire-and-forget shutdown in a background thread.

    Called from the /shutdown endpoint (and formerly the Streamlit UI) so the
    HTTP reply can flush before the current process is terminated.
    """
    def _run() -> None:
        time.sleep(0.3)  # let the HTTP response flush first
        shutdown_servers(api_port, ui_port)

    threading.Thread(target=_run, daemon=True).start()
