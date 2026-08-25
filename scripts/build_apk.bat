@echo off
chcp 936 >nul
setlocal
pushd "%~dp0.."
set "ROOT=%CD%"
popd
cd /d "%ROOT%\flutter_app"

echo ============================================
echo   AMBRACE APK build
echo ============================================

set "BUILD_TYPE=release"
for %%a in (%*) do (
    if /i "%%~a"=="--debug" set "BUILD_TYPE=debug"
    if /i "%%~a"=="debug" set "BUILD_TYPE=debug"
)

rem Locate flutter (set FLUTTER env var to full flutter.bat path if needed)
if defined FLUTTER (
    if not exist "%FLUTTER%" (
        echo [ERROR] FLUTTER var points to missing file: %FLUTTER%
        pause
        exit /b 1
    )
) else (
    where flutter >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] flutter not found. Install Flutter SDK 3.27+ and add to PATH,
        echo          or set FLUTTER env var to full flutter.bat path and retry.
        pause
        exit /b 1
    )
    set "FLUTTER=flutter"
)

echo [1/3] Fetching dependencies (pub get)...
call "%FLUTTER%" pub get
if errorlevel 1 (
    echo   [INFO] Network resolve failed, retrying with mirror pub.flutter-io.cn...
    set "PUB_HOSTED_URL=https://pub.flutter-io.cn"
    set "FLUTTER_STORAGE_BASE_URL=https://storage.flutter-io.cn"
    call "%FLUTTER%" pub get
    if errorlevel 1 (
        echo [ERROR] pub get failed. Check network - overseas: no mirror; CN: ensure pub.flutter-io.cn reachable.
        pause
        exit /b 1
    )
)

echo [2/3] Building %BUILD_TYPE% APK (first run ~5-15 min)...
call "%FLUTTER%" build apk --%BUILD_TYPE%
if errorlevel 1 (
    echo [ERROR] Build failed. Check logs above.
    pause
    exit /b 1
)

echo [3/3] Copying artifact to output...
set "OUT_DIR=%ROOT%\output"
if not exist "%OUT_DIR%" mkdir "%OUT_DIR%"
set "SRC_APK=build\app\outputs\flutter-apk\app-%BUILD_TYPE%.apk"
if not exist "%SRC_APK%" (
    echo [ERROR] Artifact not found: %SRC_APK%
    pause
    exit /b 1
)
set "DST_APK=%OUT_DIR%\ai_companion_app-%BUILD_TYPE%.apk"
copy /y "%SRC_APK%" "%DST_APK%" >nul

echo.
echo Done! APK generated:
echo   %DST_APK%
for /f "delims=" %%i in ('powershell -NoProfile -Command "[math]::Round((Get-Item '%DST_APK%').Length/1MB,2)"') do set "SIZE_MB=%%i"
echo   Size: %SIZE_MB% MB
for /f "delims=" %%h in ('powershell -NoProfile -Command "[System.BitConverter]::ToString([System.Security.Cryptography.SHA1]::Create().ComputeHash([System.IO.File]::OpenRead('%DST_APK%'))).Replace('-','')"') do set "SHA1=%%h"
echo   SHA1: %SHA1%
echo.
echo Install: copy APK to Android phone and install; server address: scripts\get_server_info.py
pause
endlocal
