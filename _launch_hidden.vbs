' ============================================================
'  Thesis RAG - hidden server launcher
'  Starts the FastAPI backend and the Streamlit UI with no
'  windows (background) so no cmd windows appear.
'  Args come from env vars set by run_all.bat:
'    LAUNCH_CWD  - project root (chdir here first)
'    API_PYTHON  - interpreter for the API
'    UI_PYTHON   - interpreter for the UI
'    API_PORT    - API port
'    UI_PORT     - UI port
' ============================================================
Option Explicit

Dim shell, fso, cwd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

cwd = shell.ExpandEnvironmentStrings("%LAUNCH_CWD%")
If cwd = "%LAUNCH_CWD%" Or cwd = "" Then cwd = fso.GetParentFolderName(WScript.ScriptFullName)
If fso.FolderExists(cwd) Then shell.CurrentDirectory = cwd

Dim apiPy, uiPy, apiPort, uiPort
apiPy  = shell.ExpandEnvironmentStrings("%API_PYTHON%")
uiPy   = shell.ExpandEnvironmentStrings("%UI_PYTHON%")
apiPort= shell.ExpandEnvironmentStrings("%API_PORT%")
uiPort = shell.ExpandEnvironmentStrings("%UI_PORT%")

If apiPy = "" Then apiPy = "python"
If uiPy  = "" Then uiPy  = "python"
If apiPort = "" Then apiPort = "8000"
If uiPort  = "" Then uiPort  = "8501"

' ---- API: run the programmatic uvicorn wrapper (reliable headless).
'      It self-redirects output to data/logs/api.log so uvicorn stays alive.
'      Port is passed as a CLI argument (immune to env inheritance issues). ----
' window style 0 = hidden, wait = False
shell.Run """" & apiPy & """ """ & cwd & "\_run_api.py"" " & apiPort, 0, False

' ---- UI: streamlit (works headless via pythonw) ----
shell.Run """" & uiPy & """ -m streamlit run """ & cwd & "\app\ui\main.py"" --server.port " & uiPort & _
         " --server.address 127.0.0.1 --server.headless true", 0, False
