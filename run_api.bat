@echo off
REM Launch the FastAPI backend on port 8000 (blocking, with auto-reload)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
