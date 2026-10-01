"""Programmatic uvicorn launcher (works headless, no console needed).

Used by the hidden background launcher (_launch_hidden.vbs / run_all.bat)
because launching `pythonw -m uvicorn ...` does not bind reliably on Windows,
and launching as a detached background process loses the console.

Two things are required for uvicorn to stay alive under pythonw:
  1. the Windows *selector* event-loop policy, and
  2. an open stdout/stderr (a console or redirected log file). When there is
     no attachable console, we redirect our own output to data/logs/api.log;
     otherwise uvicorn's asyncio loop can exit immediately after startup.
"""
import asyncio
import os
import sys

# Make sure the project root is importable + is the working dir, regardless of
# how this script is launched (VBS shell.Run, Start-Process, double-click,
# or from a different cwd). app.main and everything under app/ depend on it.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
if os.path.isdir(_HERE):
    try:
        os.chdir(_HERE)
    except OSError:
        pass

# --- Keep stdout/stderr open so uvicorn survives under pythonw. -----------
# If we are not attached to a real console (pythonw, or a detached VBS launch),
# redirect to data/logs/api.log so uvicorn's logging/asyncio has a live stream.
try:
    _has_console = sys.stdout is not None and getattr(sys.stdout, "fileno", None) is not None and os.isatty(sys.stdout.fileno())
except Exception:  # noqa: BLE001
    _has_console = False

if not _has_console:
    _log_dir = os.path.join(_HERE, "data", "logs")
    try:
        os.makedirs(_log_dir, exist_ok=True)
        _log_path = os.path.join(_log_dir, "api.log")
        _logf = open(_log_path, "a", encoding="utf-8", buffering=1)  # line buffered
        try:
            os.dup2(_logf.fileno(), sys.stdout.fileno())
            os.dup2(_logf.fileno(), sys.stderr.fileno())
        except Exception:  # noqa: BLE001
            sys.stdout = _logf
            sys.stderr = _logf
    except Exception:  # noqa: BLE001
        pass

# Windows: use the selector event loop so the server runs headless.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn  # noqa: E402

HOST = "127.0.0.1"
# Port: prefer a CLI argument, then API_PORT env, then default 8000.
PORT = 8000
if len(sys.argv) > 1 and sys.argv[1].isdigit():
    PORT = int(sys.argv[1])
else:
    PORT = int(os.environ.get("API_PORT") or 8000)

if __name__ == "__main__":
    config = uvicorn.Config(
        "app.main:app", host=HOST, port=PORT, log_level="info", loop="asyncio"
    )
    server = uvicorn.Server(config)
    # Keep running until externally terminated (taskkill / stop_all.bat).
    server.run()
