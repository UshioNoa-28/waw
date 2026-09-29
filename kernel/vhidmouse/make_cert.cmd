@echo off
rem ---------------------------------------------------------------------------
rem make_cert.cmd - create a self-signed code-signing certificate for the
rem vhidmouse driver, and sign the .sys with it.
rem
rem Run once, as Administrator. The certificate is only trusted on this
rem machine (Test Root), which is fine for test signing.
rem
rem Usage: make_cert.cmd  (expects ..\build\vhidmouse\vhidmouse.sys to exist)
rem ---------------------------------------------------------------------------
setlocal EnableExtensions

set "SRC=%~dp0"
set "SYS=%SRC%build\vhidmouse\vhidmouse.sys"
set "CERT=%SRC%vhidmouse.cer"
set "PFX=%SRC%vhidmouse.pfx"
set "PFXPASS=vhid-test"

if not exist "%SYS%" (
    echo [!] %SYS% not found. Build the driver first: build.cmd
    exit /b 1
)

echo [*] Creating self-signed certificate ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$c = New-SelfSignedCertificate -Type Custom -Subject 'CN=VhidMouse Test' -KeyUsage DigitalSignature -FriendlyName 'VhidMouse Test' -CertStoreLocation 'Cert:\CurrentUser\My' -TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.3','2.5.29.19={text}');" ^
  "Export-Certificate -Cert $c -FilePath '%CERT%' | Out-Null;" ^
  "$pw = ConvertTo-SecureString -String '%PFXPASS%' -Force -AsPlainText;" ^
  "Export-PfxCertificate -Cert $c -FilePath '%PFX%' -Password $pw | Out-Null;" ^
  "Write-Host ('[*] Thumbprint: ' + $c.Thumbprint)"
if errorlevel 1 (
    echo [!] Certificate creation failed.
    exit /b 1
)

echo [*] Signing %SYS% ...
set "SIGNTOOL="
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if exist "%VSWHERE%" (
    for /f "usebackq delims=" %%i in (`"%VSWHERE%" -latest -products * -find **\signtool.exe`) do (
        set "SIGNTOOL=%%i"
        goto :found
    )
)
where signtool >nul 2>&1
if not errorlevel 1 set "SIGNTOOL=signtool"

:found
if "%SIGNTOOL%"=="" (
    echo [!] signtool.exe not found. Install the Windows SDK / WDK.
    exit /b 1
)

"%SIGNTOOL%" sign /fd SHA256 /f "%PFX%" /p "%PFXPASS%" /t http://timestamp.digicert.com "%SYS%"
if errorlevel 1 (
    echo [!] Signing failed.
    exit /b 1
)

echo.
echo [*] Done. Certificate: %CERT%
echo     Next: install_driver.bat (as Administrator)
endlocal
exit /b 0
