@echo off
setlocal
set "APP=%~dp0"
"%APP%runtime\python.exe" "%APP%server_controller\console_app.py"
