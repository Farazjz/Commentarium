@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM ============================================================
REM  Commentarium v2.0 - background launcher (Research Studio)
REM  Starts the FastAPI backend ONLY - the v2 SPA is served by
REM  the same process at http://127.0.0.1:<PORT> (see .env, default 8010).
REM  Everything runs in the BACKGROUND via a hidden VBS launcher,
REM  then your browser opens automatically.
REM  Use stop_all.bat (or the app's "Exit & shut down" button) to stop.
REM ============================================================

cd /d "%~dp0"

if not exist "app\main.py" (
    echo [ERROR] app\main.py not found in %~dp0 - broken install.
    pause
    exit /b 1
)

REM ------------------------------------------------------------
REM 0. Find the right Python interpreter
REM    PY = normal python   PYTHONW = console-free (background)
REM ------------------------------------------------------------
set "PY=python"
set "PYTHONW=pythonw"
if exist ".venv\Scripts\pythonw.exe" (
    set "PYTHONW=%~dp0.venv\Scripts\pythonw.exe"
    set "PY=%~dp0.venv\Scripts\python.exe"
)
if exist "venv\Scripts\pythonw.exe" (
    set "PYTHONW=%~dp0venv\Scripts\pythonw.exe"
    set "PY=%~dp0venv\Scripts\python.exe"
)
if "%PY%"=="python" (
    REM bare python: resolve its real location via PATH, then use the
    REM pythonw.exe living next to it (avoids Windows Store stubs).
    set "PYDIR="
    for /f "delims=" %%W in ('where python 2^>nul') do (
        if not defined PYDIR set "PYDIR=%%~dpW"
    )
    if defined PYDIR (
        set "PY=!PYDIR!python.exe"
        if exist "!PYDIR!pythonw.exe" set "PYTHONW=!PYDIR!pythonw.exe"
    )
)

"%PY%" --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python or create a venv in this folder.
    pause
    exit /b 1
)

REM ------------------------------------------------------------
REM 1. Read the port from .env (fallback 8010)
REM ------------------------------------------------------------
set "API_PORT=8010"
for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0.env") do (
    if /i "%%A"=="PORT" set "API_PORT=%%B"
)
for /f "delims=" %%P in ("%API_PORT%") do set "API_PORT=%%~P"

set "API_URL=http://127.0.0.1:%API_PORT%"

REM ------------------------------------------------------------
REM 2. Launch the API hidden via the VBS wrapper
REM ------------------------------------------------------------
set "LAUNCH_CWD=%~dp0"
set "API_PYTHON=%PYTHONW%"
set "API_PORT=%API_PORT%"

cscript //nologo "%~dp0_launch_hidden.vbs" >nul 2>&1
if errorlevel 1 (
    echo [WARN] Hidden launcher failed - falling back to a visible console.
    start "" "%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %API_PORT%
)

REM ------------------------------------------------------------
REM 3. Wait for the API to come up, then open the browser
REM ------------------------------------------------------------
echo Waiting for the Commentarium v2 server to come online...
set /a tries=0
:waitapi
ping -n 2 127.0.0.1 >nul
netstat -ano 2>nul | findstr /r /c:":%API_PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 goto apiup
set /a tries+=1
if %tries% lss 30 goto waitapi
echo [WARN] Server did not start within 30s. Check data\logs\app.log and data\logs\api.log.
goto afterwait
:apiup
echo Server is up at %API_URL%
:afterwait

echo Opening the Research Studio UI in your browser...
start "" "%API_URL%"

echo.
echo  Commentarium v2 is running in the BACKGROUND (no windows).
echo  To stop it later, double-click stop_all.bat in this folder,
echo  or use the "Exit & shut down" button in the app.
echo.
ping -n 4 127.0.0.1 >nul
exit /b 0
