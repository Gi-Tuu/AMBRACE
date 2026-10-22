@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0.."
if errorlevel 1 goto :abort_root

rem SEC-P0-3: only uninstall from <root>\scripts\uninstall.bat of a real project root.
rem Internal .git/AGENTS are not acceptable proof: release packages do not ship them.
set "ROOT=%CD%"
set "SCRIPT_DIR=%~dp0"
for %%I in ("%~dp0.") do set "SCRIPT_DIR=%%~fI\"
if "%ROOT:~-1%"=="\" (set "EXPECTED_SCRIPT_DIR=%ROOT%scripts\") else (set "EXPECTED_SCRIPT_DIR=%ROOT%\scripts\")
if /i not "%SCRIPT_DIR%"=="%EXPECTED_SCRIPT_DIR%" goto :abort_root
if not exist "%ROOT%\VERSION" goto :abort_root
if not exist "%ROOT%\backend\app\main.py" goto :abort_root
if not exist "%ROOT%\backend\" goto :abort_root
if not exist "%ROOT%\scripts\" goto :abort_root

set "PURGE=0"
for %%a in (%*) do (
    if /i "%%~a"=="--purge" set "PURGE=1"
    if /i "%%~a"=="/purge" set "PURGE=1"
)

echo ============================================
echo   AMBRACE uninstall (Windows)
echo ============================================

echo [1/3] Stopping server and watchdog...
if exist "backend\.venv\Scripts\python.exe" (
    "backend\.venv\Scripts\python.exe" scripts\server_manager.py stop
) else (
    python scripts\server_manager.py stop 2>nul
)
if errorlevel 1 goto :abort_stop

rem server_manager.py stop has historically returned 0 even when it could not kill
rem everything. Verify the authoritative listeners are really gone before touching files.
where netstat >nul 2>nul
if errorlevel 1 goto :abort_stop_check
set "PORT_BUSY="
for /f "tokens=*" %%L in ('netstat -ano ^| findstr /R /C:":8000 .*LISTENING" /C:":8765 .*LISTENING" /C:":8766 .*LISTENING"') do set "PORT_BUSY=1"
if defined PORT_BUSY goto :abort_stop

echo [2/3] Cleaning project files...
set "KEEP_DIR="
set "HAS_ENV=0"
set "HAS_DATA=0"
set "HAS_MODELS=0"

if "%PURGE%"=="1" goto :mode_purge
echo   [MODE] Keep data: .env, backend\data, backend\models preserved
set "KEEP_DIR=%TEMP%\aic_uninstall_keep_%RANDOM%_%RANDOM%_%RANDOM%"
mkdir "%KEEP_DIR%"
if errorlevel 1 goto :abort_keepdir
rem R1: KEEP_DIR must be provably outside the tree we are about to delete.
call :check_keep_dir
if errorlevel 1 goto :abort_keep_unsafe
if exist ".env" (
    set "HAS_ENV=1"
    move ".env" "%KEEP_DIR%\.env" >nul
    if errorlevel 1 goto :abort_keep_move
)
if exist "backend\data" (
    set "HAS_DATA=1"
    move "backend\data" "%KEEP_DIR%\data" >nul
    if errorlevel 1 goto :abort_keep_move
)
if exist "backend\models" (
    set "HAS_MODELS=1"
    move "backend\models" "%KEEP_DIR%\models" >nul
    if errorlevel 1 goto :abort_keep_move
)
goto :mode_ready

:mode_purge
echo   [MODE] Purge --purge: data, config, models, dependencies all removed

:mode_ready

rem R1: re-check immediately before recursive deletion (TEMP may be swapped/aliased).
if "%KEEP_DIR%"=="" goto :after_keep_boundary_check
call :check_keep_dir
if errorlevel 1 goto :abort_keep_unsafe
:after_keep_boundary_check

rem Re-confirm the validated root immediately before recursive deletion.
if /i not "%CD%"=="%ROOT%" goto :abort_before_delete
if not exist "%ROOT%\backend\app\main.py" goto :abort_before_delete

rem Remove all subdirs and root files except scripts (uninstaller location).
rem Keep mode also preserves backups as-is; purge may remove it after root validation.
if "%PURGE%"=="1" (
    for /d %%d in (*) do (
        if /i not "%%d"=="scripts" rd /s /q "%%d" 2>nul
    )
) else (
    for /d %%d in (*) do (
        if /i not "%%d"=="scripts" if /i not "%%d"=="backups" rd /s /q "%%d" 2>nul
    )
)
for %%f in (*) do del /q "%%f" 2>nul

if "%PURGE%"=="1" goto :after_restore
rem R1: restore moves also read from KEEP_DIR; re-check before touching project paths.
if "%KEEP_DIR%"=="" goto :after_restore_boundary_check
call :check_keep_dir
if errorlevel 1 goto :abort_keep_unsafe
:after_restore_boundary_check
rem Restore kept data and config; any failure keeps KEEP_DIR as the unique copy.
mkdir "backend"
if errorlevel 1 goto :abort_restore
if "%HAS_ENV%"=="1" (
    move "%KEEP_DIR%\.env" ".env" >nul
    if errorlevel 1 goto :abort_restore
    if not exist ".env" goto :abort_restore
    if exist "%KEEP_DIR%\.env" goto :abort_restore
)
if "%HAS_DATA%"=="1" (
    move "%KEEP_DIR%\data" "backend\data" >nul
    if errorlevel 1 goto :abort_restore
    if not exist "backend\data\" goto :abort_restore
    if exist "%KEEP_DIR%\data" goto :abort_restore
)
if "%HAS_MODELS%"=="1" (
    move "%KEEP_DIR%\models" "backend\models" >nul
    if errorlevel 1 goto :abort_restore
    if not exist "backend\models\" goto :abort_restore
    if exist "%KEEP_DIR%\models" goto :abort_restore
)
rem Only clear the temporary keep area after every restored item is verified.
rd /s /q "%KEEP_DIR%" 2>nul
if exist "%KEEP_DIR%" echo   [WARN] Temporary keep directory could not be removed: %KEEP_DIR%
set "KEEP_DIR="

:after_restore
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
    rem Delayed self-delete of scripts dir in a separate process, without a new window.
    start "" /b cmd /c "ping 127.0.0.1 -n 3 >nul & rd /s /q "%~dp0""
)
endlocal
exit /b 0

:abort_root
echo [ERROR] Uninstall refused: run this script from ^<project-root^>\scripts\uninstall.bat
echo         and the root must contain VERSION and backend\app\main.py.
exit /b 1

:abort_stop
echo [ERROR] Uninstall aborted: the server/watchdog is still running.
echo         No files were removed.
exit /b 1

:abort_stop_check
echo [ERROR] Uninstall aborted: could not verify that the server stopped ^(netstat unavailable^).
echo         No files were removed.
exit /b 1

:abort_keepdir
echo [ERROR] Uninstall aborted: could not create a private keep directory under TEMP.
echo         No files were removed.
exit /b 1

:abort_keep_move
echo [ERROR] Uninstall aborted: could not preserve project data.
echo         Already moved items remain in: %KEEP_DIR%
echo         Unmoved items were left untouched; nothing was deleted.
exit /b 1

:abort_keep_unsafe
echo [ERROR] Uninstall aborted: the keep directory is inside or aliases into the project tree,
echo         or its real path could not be verified. No files were deleted.
if not "%KEEP_DIR%"=="" echo         Any items already moved remain in: %KEEP_DIR%
exit /b 1

:abort_before_delete
echo [ERROR] Uninstall aborted: the validated project root changed or required files disappeared.
echo         Nothing was deleted.
if not "%KEEP_DIR%"=="" echo         Preserved items remain in: %KEEP_DIR%
exit /b 1

:abort_restore
echo [ERROR] Uninstall aborted: data restore failed. The keep directory was NOT removed.
echo         Recover your files from: %KEEP_DIR%
exit /b 1

:check_keep_dir
rem R1: compare both lexical and resolved paths; realpath resolves Windows junctions.
rem Reject KEEP_DIR inside (or equal to) ROOT in either form; unknown drives/errors abort.
if "%KEEP_DIR%"=="" exit /b 1
if not exist "backend\.venv\Scripts\python.exe" goto :check_keep_dir_fallback
"backend\.venv\Scripts\python.exe" -c "import os,sys;ins=lambda c,p:(os.path.commonpath([c,p])==p) if os.path.splitdrive(c)[0].lower()==os.path.splitdrive(p)[0].lower() else False;r=os.path.abspath(sys.argv[1]);rr=os.path.realpath(sys.argv[1]);k=os.path.abspath(sys.argv[2]);kr=os.path.realpath(sys.argv[2]);sys.exit(1 if (ins(k,r) or ins(k,rr) or ins(kr,r) or ins(kr,rr)) else 0)" "%ROOT%" "%KEEP_DIR%" >nul 2>nul
exit /b %errorlevel%

:check_keep_dir_fallback
python -c "import os,sys;ins=lambda c,p:(os.path.commonpath([c,p])==p) if os.path.splitdrive(c)[0].lower()==os.path.splitdrive(p)[0].lower() else False;r=os.path.abspath(sys.argv[1]);rr=os.path.realpath(sys.argv[1]);k=os.path.abspath(sys.argv[2]);kr=os.path.realpath(sys.argv[2]);sys.exit(1 if (ins(k,r) or ins(k,rr) or ins(kr,r) or ins(kr,rr)) else 0)" "%ROOT%" "%KEEP_DIR%" >nul 2>nul
exit /b %errorlevel%
