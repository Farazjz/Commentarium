@echo off
setlocal EnableExtensions
REM ============================================================
REM  Commentarium v2.0 - stop the background API server
REM  Kills whatever is listening on the configured port
REM  (PORT in .env, default 8010). Safe to run always.
REM ============================================================

cd /d "%~dp0"

set "API_PORT=8010"
if exist "%~dp0.env" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0.env") do (
        if /i "%%A"=="PORT" set "API_PORT=%%B"
    )
)
for /f "delims=" %%P in ("%API_PORT%") do set "API_PORT=%%~P"

echo Stopping Commentarium v2 server (port %API_PORT%)...
set "KILLED="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%API_PORT% .*LISTENING"') do (
    if not "%%P"=="0" (
        taskkill /PID %%P /T /F >nul 2>&1
        set "KILLED=1"
    )
)
if not defined KILLED echo   (nothing was listening on %API_PORT%)
echo Done.
ping -n 2 127.0.0.1 >nul
exit /b 0
