@echo off
rem UTF-8/GBK-safe: all messages below are ASCII
setlocal
cd /d "%~dp0.."
echo ============================================
echo   AMBRACE one-click deploy (Windows)
echo ============================================

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.12+ with "Add to PATH" checked.
    pause
    exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,12) else 1)"
if errorlevel 1 (
    echo [ERROR] Python 3.12+ (3.14 also OK) is required.
    pause
    exit /b 1
)

echo [1/4] Creating virtual environment...
if not exist backend\.venv (
    python -m venv backend\.venv
)

echo [2/4] Installing dependencies (first run ~2-5 min)...
backend\.venv\Scripts\python.exe -m pip install --upgrade pip >nul
backend\.venv\Scripts\pip.exe install -r backend\requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency install failed. Check your network and retry.
    pause
    exit /b 1
)

echo [3/4] Generating config file...
if not exist .env (
    copy .env.example .env >nul
    echo   .env created - all options included, empty means defaults.
)

echo [4/4] Self-check...
if not exist backend\models\bge-m3 (
    echo   [WARN] Missing backend\models\bge-m3 - memory system will not start.
    echo          Ensure the directory is complete in the package, or re-extract.
) else (
    echo   [OK] Vector model directory exists.
)
if not exist backend\data\sqlite (
    echo   [INFO] Database will be auto-created on first start.
)

echo.
echo Setup complete! Next steps:
echo   1. Start server: backend\.venv\Scripts\python.exe scripts\server_manager.py start
echo   2. Get connection info: backend\.venv\Scripts\python.exe scripts\get_server_info.py
echo   3. Login as admin, fill LLM Key in Settings -> API Config (required for chat).
echo   4. Install APK on phone and fill server address in settings.
pause
endlocal
