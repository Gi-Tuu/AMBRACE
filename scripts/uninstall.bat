@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0.."
echo ============================================
echo   AMBRACE uninstall (Windows)
echo ============================================

set "PURGE=0"
for %%a in (%*) do (
    if /i "%%~a"=="--purge" set "PURGE=1"
    if /i "%%~a"=="/purge" set "PURGE=1"
)

echo [1/3] Stopping server and watchdog...
if exist "backend\.venv\Scripts\python.exe" (
    "backend\.venv\Scripts\python.exe" scripts\server_manager.py stop
) else (
    python scripts\server_manager.py stop 2>nul
)

echo [2/3] Cleaning project files...
set "KEEP_DIR=%TEMP%\aic_uninstall_keep_%RANDOM%"
mkdir "%KEEP_DIR%" 2>nul

if "%PURGE%"=="1" (
    echo   [MODE] Purge --purge: data, config, models, dependencies all removed
) else (
    echo   [MODE] Keep data: .env, backend\data, backend\models preserved
    if exist ".env" move ".env" "%KEEP_DIR%\.env" >nul
    if exist "backend\data" move "backend\data" "%KEEP_DIR%\data" >nul
    if exist "backend\models" move "backend\models" "%KEEP_DIR%\models" >nul
)

rem Remove all subdirs and root files except scripts (uninstaller location)
for /d %%d in (*) do (
    if /i not "%%d"=="scripts" rd /s /q "%%d" 2>nul
)
for %%f in (*) do del /q "%%f" 2>nul

if not "%PURGE%"=="1" (
    rem Restore kept data and config
    mkdir "backend" 2>nul
    if exist "%KEEP_DIR%\.env" move "%KEEP_DIR%\.env" ".env" >nul
    if exist "%KEEP_DIR%\data" move "%KEEP_DIR%\data" "backend\data" >nul
    if exist "%KEEP_DIR%\models" move "%KEEP_DIR%\models" "backend\models" >nul
)
rd /s /q "%KEEP_DIR%" 2>nul

echo [3/3] Done
if "%PURGE%"=="1" (
    echo   Fully uninstalled. To redeploy: re-extract package and run setup.bat
) else (
    echo   Program removed; data and config kept at:
    echo     - .env
    echo     - backend\data - database / uploads
    echo     - backend\models - vector models
    echo   Uninstaller kept at scripts\uninstall.bat,
    echo   To fully remove them: scripts\uninstall.bat --purge
)

if "%PURGE%"=="1" (
    rem Delayed self-delete of scripts dir in separate process
    start "" /min cmd /c "ping 127.0.0.1 -n 3 >nul & rd /s /q "%~dp0""
)
endlocal
