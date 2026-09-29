@echo off
rem ===========================================================================
rem install_driver.bat - one-click install of the vhidmouse driver for LOCAL
rem testing.
rem
rem What it does:
rem   1. builds the driver if needed
rem   2. creates + trusts a self-signed cert, signs the .sys
rem   3. enables test signing (requires a reboot the first time)
rem   4. installs the driver as a root-enumerated device and starts it
rem
rem WARNING: test signing must stay ON for the driver to load, and Vanguard
rem REFUSES TO RUN while test signing is on. This path is for verifying the
rem aim pipeline (detector + aim engine + virtual HID input) on anything that
rem is NOT Vanguard-protected. See kernel\README.md for the real options.
rem
rem Run as Administrator.
rem ===========================================================================
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
    echo [!] This script must be run as Administrator.
    echo     Right-click install_driver.bat -^> Run as administrator.
    pause
    exit /b 1
)

echo.
echo ================================================
echo   VhidMouse driver installer (test signing)
echo ================================================
echo.

set "KDIR=%~dp0kernel\vhidmouse"
set "SYS=%KDIR%\build\vhidmouse\vhidmouse.sys"
set "CERT=%KDIR%\vhidmouse.cer"
set "INF=%KDIR%\vhidmouse.inf"

REM ---- [1/5] build ---------------------------------------------------------
echo [1/5] Checking driver binary...
if not exist "%SYS%" (
    echo       Not built yet, building now...
    call "%KDIR%\build.cmd"
    if errorlevel 1 (
        echo [!] Driver build failed. Install VS2022 + WDK and retry.
        pause
        exit /b 1
    )
)
echo       OK: %SYS%
echo.

REM ---- [2/5] certificate ---------------------------------------------------
echo [2/5] Creating and trusting a self-signed certificate...
if not exist "%CERT%" (
    call "%KDIR%\make_cert.cmd"
    if errorlevel 1 (
        echo [!] Certificate creation failed.
        pause
        exit /b 1
    )
)

certutil -addstore -f "Root" "%CERT%" >nul
if errorlevel 1 ( echo [!] Failed to trust certificate in Root store. & pause & exit /b 1 )
certutil -addstore -f "TrustedPublisher" "%CERT%" >nul
if errorlevel 1 ( echo [!] Failed to trust certificate in TrustedPublisher store. & pause & exit /b 1 )
echo       OK.
echo.

REM ---- [3/5] test signing --------------------------------------------------
echo [3/5] Enabling test signing...
bcdedit /enum "{current}" | findstr /i "testsigning" | findstr /i "Yes" >nul
if errorlevel 1 (
    bcdedit /set testsigning on
    if errorlevel 1 ( echo [!] Could not enable test signing. & pause & exit /b 1 )
    echo.
    echo       Test signing was just ENABLED.
    echo       >>> You must REBOOT now, then run this script again. <<<
    echo.
    echo       After the reboot, Vanguard-protected games will refuse to run
    echo       until you disable it: bcdedit /set testsigning off
    echo.
    pause
    exit /b 0
)
echo       Already on.
echo.

REM ---- [4/5] install -------------------------------------------------------
echo [4/5] Installing the driver...
sc query vhidmouse >nul 2>&1
if not errorlevel 1 (
    sc stop vhidmouse >nul 2>&1
    sc delete vhidmouse >nul 2>&1
)

pnputil /add-driver "%INF%" /install
if errorlevel 1 (
    echo [!] pnputil failed. Trying devcon fallback...
    where devcon >nul 2>&1
    if errorlevel 1 (
        echo [!] Neither pnputil nor devcon succeeded.
        pause
        exit /b 1
    )
    devcon update "%INF%" "Root\VhidMouse"
    if errorlevel 1 ( echo [!] devcon failed. & pause & exit /b 1 )
)
echo.

REM ---- [5/5] start ---------------------------------------------------------
echo [5/5] Starting the driver...
sc query vhidmouse | findstr /i "RUNNING" >nul
if errorlevel 1 (
    sc start vhidmouse
    if errorlevel 1 (
        echo [!] Could not start vhidmouse. Check: sc query vhidmouse
        echo     and the Windows event log.
        pause
        exit /b 1
    )
)

echo.
echo ================================================
echo   Driver installed and running.
echo.
echo   Verify:
echo     sc query vhidmouse
echo     powershell "Get-PnpDevice -FriendlyName '*Vhid*'"
echo.
echo   Then run:
echo     dist\ValAim\ValAim.exe --debug
echo.
echo   To uninstall: uninstall_driver.bat
echo ================================================
echo.
pause
endlocal
exit /b 0
