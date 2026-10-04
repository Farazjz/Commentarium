@echo off
setlocal EnableExtensions EnableDelayedExpansion
REM ============================================================
REM  Commentarium - background launcher for the API + UI
REM  Starts the FastAPI backend and the Streamlit UI fully in the
REM  BACKGROUND (no console windows) via a hidden VBS launcher,
REM  then opens the browser. Use stop_all.bat (or the UI's
REM  "Exit & shut down servers" button) to stop both servers.
REM ============================================================

cd /d "%~dp0"

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
) else (
    REM python is already a full path -> derive pythonw.exe from the same folder
    for /f "delims=" %%D in ("%PY%") do set "PYDIR=%%~dpD"
    if exist "%PYDIR%pythonw.exe" set "PYTHONW=%PYDIR%pythonw.exe"
)

"%PY%" --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python or create a venv in this folder.
    pause
    exit /b 1
)

REM ------------------------------------------------------------
REM 1. Read ports from config (fall back to defaults 8000 / 8501)
REM ------------------------------------------------------------
set "API_PORT=8000"
set "UI_PORT=8501"
for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0.env") do (
    if /i "%%A"=="PORT" set "API_PORT=%%B"
    if /i "%%A"=="UI_PORT" set "UI_PORT=%%B"
)
REM strip trailing whitespace / CR, and potential surrounding quotes
for /f "delims=" %%P in ("%API_PORT%") do set "API_PORT=%%~P"
for /f "delims=" %%P in ("%UI_PORT%") do set "UI_PORT=%%~P"

set "API_URL=http://127.0.0.1:%API_PORT%"
set "UI_URL=http://127.0.0.1:%UI_PORT%"

REM ------------------------------------------------------------
REM 2. Launch both servers hidden via the VBS wrapper
REM ------------------------------------------------------------
set "LAUNCH_CWD=%~dp0"
set "API_PYTHON=%PYTHONW%"
set "UI_PYTHON=%PYTHONW%"
set "API_PORT=%API_PORT%"
set "UI_PORT=%UI_PORT%"

cscript //nologo "%~dp0_launch_hidden.vbs" >nul 2>&1
if errorlevel 1 (
    echo [WARN] Hidden launcher failed - falling back to a visible console.
    start "" "%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %API_PORT%
    start "" "%PY%" -m streamlit run "%~dp0app\ui\main.py" --server.port %UI_PORT% --server.address 127.0.0.1 --server.headless true
)

REM ------------------------------------------------------------
REM 3. Wait for the API to come up, then open the browser
REM ------------------------------------------------------------
echo Waiting for the API to come online...
set /a tries=0
:waitapi
timeout /t 1 /nobreak >nul
netstat -ano 2>nul | findstr /r /c:":%API_PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 goto apiup
set /a tries+=1
if %tries% lss 30 goto waitapi
echo [WARN] API did not start within 30s. Check data\logs\app.log for errors.
goto afterwait
:apiup
echo API is up at %API_URL%
:afterwait

echo Opening the UI in your browser...
start "" "%UI_URL%"

echo.
echo  Servers are running in the BACKGROUND (no windows).
echo  To stop them later, double-click stop_all.bat in this folder,
echo  or use the "Exit & shut down servers" button in the UI sidebar.
echo.
timeout /t 3 /nobreak >nul
exit /b 0
