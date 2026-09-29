@echo off
rem ===========================================================================
rem uninstall_driver.bat - remove the vhidmouse driver and its certificate.
rem Run as Administrator.
rem ===========================================================================
setlocal EnableExtensions

cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
    echo [!] Run as Administrator.
    pause
    exit /b 1
)

set "KDIR=%~dp0kernel\vhidmouse"
set "CERT=%KDIR%\vhidmouse.cer"

echo [*] Stopping and deleting the service...
sc stop vhidmouse >nul 2>&1
sc delete vhidmouse >nul 2>&1

echo [*] Removing the device...
pnputil /enum-drivers | findstr /i "vhidmouse" >nul
if not errorlevel 1 (
    pnputil /delete-driver vhidmouse.inf /uninstall /force >nul 2>&1
)

if exist "%CERT%" (
    echo [*] Removing the certificate from the trust stores...
    certutil -delstore "Root" "VhidMouse Test" >nul 2>&1
    certutil -delstore "TrustedPublisher" "VhidMouse Test" >nul 2>&1
)

echo.
echo [*] Done.
echo.
echo Note: test signing is still ON. To restore Vanguard compatibility:
echo     bcdedit /set testsigning off
echo     (reboot)
echo.
pause
endlocal
exit /b 0
