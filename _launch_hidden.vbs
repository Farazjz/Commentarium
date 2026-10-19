' ============================================================
'  Commentarium v2.0 - hidden server launcher
'  Starts ONLY the FastAPI backend (the v2 SPA is served by the
'  same process) with no windows, so no cmd windows appear.
'  Args come from env vars set by run.bat:
'    LAUNCH_CWD  - project root (chdir here first)
'    API_PYTHON  - interpreter for the API
'    API_PORT    - API port (default 8010)
' ============================================================
Option Explicit

Dim shell, fso, cwd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

cwd = shell.ExpandEnvironmentStrings("%LAUNCH_CWD%")
If cwd = "%LAUNCH_CWD%" Or cwd = "" Then cwd = fso.GetParentFolderName(WScript.ScriptFullName)
If fso.FolderExists(cwd) Then shell.CurrentDirectory = cwd

Dim apiPy, apiPort
apiPy   = shell.ExpandEnvironmentStrings("%API_PYTHON%")
apiPort = shell.ExpandEnvironmentStrings("%API_PORT%")

If apiPy = "" Then apiPy = "python"
If apiPort = "" Then apiPort = "8010"

' ---- API: run the programmatic uvicorn wrapper (_run_api.py, which
'      self-redirects output to data/logs/api.log so uvicorn stays
'      alive headless). Port passed as CLI argument, not env. ----
' window style 0 = hidden, wait = False
shell.Run """" & apiPy & """ """ & cwd & "\_run_api.py"" " & apiPort, 0, False
