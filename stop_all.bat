@echo off
setlocal EnableExtensions
REM ============================================================
REM  Thesis RAG - stop background API + UI servers
REM  Kills whatever is listening on the configured API/UI ports.
REM  Safe to run even if nothing is running.
REM ============================================================

cd /d "%~dp0"

REM Read ports from .env (fall back to 8000 / 8501)
set "API_PORT=8000"
set "UI_PORT=8501"
if exist "%~dp0.env" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0.env") do (
        if /i "%%A"=="PORT" set "API_PORT=%%B"
        if /i "%%A"=="UI_PORT" set "UI_PORT=%%B"
    )
)
for /f "delims=" %%P in ("%API_PORT%") do set "API_PORT=%%P"
for /f "delims=" %%P in ("%UI_PORT%") do set "UI_PORT=%%P"

echo Stopping Thesis RAG servers (ports %API_PORT% / %UI_PORT%)...
set "KILLED="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%API_PORT% .*LISTENING" /c:":%UI_PORT% .*LISTENING"') do (
    if not "%%P"=="0" (
        taskkill /PID %%P /T /F >nul 2>&1
        set "KILLED=1"
    )
)
if not defined KILLED echo   (nothing was listening on %API_PORT% / %UI_PORT%)
echo Done.
timeout /t 1 /nobreak >nul
exit /b 0
