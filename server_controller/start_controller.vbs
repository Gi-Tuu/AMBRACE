Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
projectRoot = fso.GetParentFolderName(scriptDir)
Set ws = CreateObject("WScript.Shell")
ws.Run """" & projectRoot & "\backend\.venv\Scripts\pythonw.exe"" """ & scriptDir & "\server_controller.py""", 1, False
