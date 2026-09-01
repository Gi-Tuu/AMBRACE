@echo off
rem ASCII-safe
title AMBRACE Server restarting...

echo [1/3] Restart via server_manager (stops watchdog+uvicorn, starts single instance)...
cd /d D:\AICompanionServer
call backend\.venv\Scripts\python.exe scripts\server_manager.py restart

echo [2/3] Waiting for server ready...
timeout /t 8 /nobreak >nul

:check
ping -n 2 127.0.0.1 >nul
powershell -Command "try { $r = Invoke-WebRequest -Uri 'http://localhost:8000/' -TimeoutSec 3 -UseBasicParsing; if ($r.StatusCode -eq 200) { echo Status: running; exit 0 } else { echo Status: error; exit 1 } } catch { echo Status: not ready; exit 1 }"
if %errorlevel% neq 0 goto check

echo [3/3] Server ready. [OK]
echo   Address: http://192.168.1.21:8000
echo.
call backend\.venv\Scripts\python.exe scripts\server_manager.py status
timeout /t 5 /nobreak >nul
exit