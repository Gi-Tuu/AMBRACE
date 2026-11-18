@echo off
setlocal
set "APP=%~dp0"
set "AMBRACE_SERVER_DIR=%APP%backend"
if exist "%APP%runtime\pythonw.exe" (set "AMBRACE_PYTHONW=%APP%runtime\pythonw.exe") else (set "AMBRACE_PYTHONW=%APP%runtime\python.exe")
start "" "%AMBRACE_PYTHONW%" "%APP%backend\scripts\watchdog.py"
