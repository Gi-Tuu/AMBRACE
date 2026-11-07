@echo off
echo ========================================
echo  AMBRACE - firewall port allow script
echo ========================================
echo.
echo Run this script as Administrator.
echo.
netsh advfirewall firewall add rule name="AMBRACE Server" dir=in action=allow protocol=TCP localport=8000
if %errorlevel%==0 (
    echo [OK] Port 8000 allowed.
) else (
    echo [FAILED] Re-run as Administrator.
)
pause
