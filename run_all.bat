@echo off
setlocal EnableExtensions
REM ============================================================
REM  Thesis RAG - one-click launcher for the API + UI
REM  Starts the FastAPI backend and the Streamlit UI together
REM  in a single console, then opens the browser for you.
REM  Close this window (or press any key) to stop both servers.
REM ============================================================

cd /d "%~dp0"

REM ------------------------------------------------------------
REM 0. Find the right Python
REM    Prefer a venv in this folder, then `python`, then `py`.
REM ------------------------------------------------------------
set "PY=python"
if exist ".venv\Scripts\python.exe" (
    set "PY=%~dp0.venv\Scripts\python.exe"
) else if exist "venv\Scripts\python.exe" (
    set "PY=%~dp0venv\Scripts\python.exe"
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
REM strip any trailing whitespace / CR
for /f "delims=" %%P in ("%API_PORT%") do set "API_PORT=%%P"
for /f "delims=" %%P in ("%UI_PORT%") do set "UI_PORT=%%P"

set "API_URL=http://127.0.0.1:%API_PORT%"
set "UI_URL=http://127.0.0.1:%UI_PORT%"

echo.
echo  ============================================
echo   Thesis RAG - starting servers
echo   API : %API_URL%
echo   UI  : %UI_URL%
echo   Close this window to stop everything.
echo  ============================================
echo.

REM ------------------------------------------------------------
REM 2. Quick check: are the ports already in use?
REM ------------------------------------------------------------
netstat -ano | findstr /r /c:":%API_PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo [WARN] Port %API_PORT% is already in use. The API may already be running.
)

REM ------------------------------------------------------------
REM 3. Start the FastAPI backend (own window titled "API")
REM ------------------------------------------------------------
start "Thesis RAG - API (%API_PORT%)" "%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %API_PORT%

REM ------------------------------------------------------------
REM 4. Start the Streamlit UI (own window titled "UI")
REM ------------------------------------------------------------
start "Thesis RAG - UI (%UI_PORT%)" "%PY%" -m streamlit run app/ui/main.py --server.port %UI_PORT% --server.address 127.0.0.1 --server.headless true

REM ------------------------------------------------------------
REM 5. Wait for the API to come up, then open the browser
REM ------------------------------------------------------------
echo Waiting for the API to come online...
set /a tries=0
:waitapi
timeout /t 1 /nobreak >nul
netstat -ano | findstr /r /c:":%API_PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 goto apiup
set /a tries+=1
if %tries% lss 30 goto waitapi
echo [WARN] API did not start within 30s. Check the API window for errors.
goto afterwait
:apiup
echo API is up at %API_URL%
:afterwait

echo Opening the UI in your browser...
start "" "%UI_URL%"

echo.
echo Both servers are running. You can:
echo   - Close this window to shut everything down
echo   - Or use the "Exit & shut down servers" button in the UI sidebar
echo.
echo Press any key to stop all servers...
pause >nul

REM ------------------------------------------------------------
REM 6. On exit: stop the servers that are listening on our ports
REM ------------------------------------------------------------
echo Stopping servers...
set "KILLED="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%API_PORT% .*LISTENING" /c:":%UI_PORT% .*LISTENING"') do (
    if not "%%P"=="0" (
        taskkill /PID %%P /T /F >nul 2>&1
        set "KILLED=1"
    )
)
if not defined KILLED echo   (nothing was listening on %API_PORT% / %UI_PORT%)
echo Done. Goodbye!
timeout /t 1 /nobreak >nul
exit /b 0
