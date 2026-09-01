@echo off
echo ========================================
echo  AMBRACE Server v3.1.0
echo ========================================
echo.
cd /d D:\AICompanionServer
echo Starting via server_manager (single-instance uvicorn + watchdog) at http://0.0.0.0:8000
echo.
call backend\.venv\Scripts\python.exe scripts\server_manager.py start
echo.
echo Server starting, model loads ~30-60s. Status:
call backend\.venv\Scripts\python.exe scripts\server_manager.py status
echo.
pause
