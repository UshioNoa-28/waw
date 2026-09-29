@echo off
rem ---------------------------------------------------------------------------
rem build.cmd - build the VHF virtual mouse driver.
rem
rem Requires: Visual Studio 2022 (with the "Desktop development with C++"
rem workload) plus the Windows Driver Kit.
rem Works from a plain Administrator cmd.
rem ---------------------------------------------------------------------------
setlocal EnableExtensions

set "SRC=%~dp0"
set "OUT=%SRC%build\vhidmouse\"

if not exist "%OUT%" mkdir "%OUT%"

REM ---- locate MSBuild -------------------------------------------------------
REM Check the usual Visual Studio 2022 install locations first.  vswhere is
REM only a fallback, because it happily returns unrelated MSBuild copies
REM (e.g. the one bundled with SQL Server Management Studio).
set "MSBUILD="
set "PF86=%ProgramFiles(x86)%"

for %%p in (
    "%ProgramFiles%\Microsoft Visual Studio\2022\Enterprise\MSBuild\Current\Bin\amd64\MSBuild.exe"
    "%ProgramFiles%\Microsoft Visual Studio\2022\Professional\MSBuild\Current\Bin\amd64\MSBuild.exe"
    "%ProgramFiles%\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\amd64\MSBuild.exe"
    "%PF86%\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\amd64\MSBuild.exe"
    "%ProgramFiles%\Microsoft Visual Studio\2022\Enterprise\MSBuild\Current\Bin\MSBuild.exe"
    "%ProgramFiles%\Microsoft Visual Studio\2022\Professional\MSBuild\Current\Bin\MSBuild.exe"
    "%ProgramFiles%\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe"
) do (
    if not defined MSBUILD if exist %%p set "MSBUILD=%%p"
)

if not defined MSBUILD (
    echo [!] Could not find Visual Studio 2022 MSBuild in the usual locations.
    echo     Install VS2022 with the "Desktop development with C++" workload.
    exit /b 1
)
echo [*] MSBuild: %MSBUILD%

REM ---- locate WDK -----------------------------------------------------------
REM %ProgramFiles(x86)% contains "(x86)"; parentheses break cmd parsing inside
REM if/for blocks, so we already normalised it into PF86 above.
set "WDK_INC=%PF86%\Windows Kits\10\Include"
call :check_wdk
if not "%HAVE_WDK%"=="1" goto :no_wdk
echo [*] WDK found.

echo [*] Building vhidmouse.sys ...
"%MSBUILD%" "%SRC%vhidmouse.vcxproj" /p:Configuration=Release /p:Platform=x64 /p:OutDir="%OUT%" /nologo /v:minimal
if errorlevel 1 (
    echo [!] Build failed. Scroll up for the compiler/linker error.
    exit /b 1
)

if not exist "%OUT%vhidmouse.sys" (
    echo [!] Build reported success but %OUT%vhidmouse.sys is missing.
    exit /b 1
)

echo.
echo [*] Built: %OUT%vhidmouse.sys
echo.
endlocal
exit /b 0

REM ---------------------------------------------------------------------------
:check_wdk
set "HAVE_WDK=0"
if not exist "%WDK_INC%" goto :eof
for /f "delims=" %%d in ('dir /b /ad /o-n "%WDK_INC%" 2^>nul') do (
    if exist "%WDK_INC%\%%d\km\wdf.h" set "HAVE_WDK=1"
)
goto :eof

:no_wdk
echo [!] Windows Driver Kit not found.
echo     Expected a file like:
echo       %WDK_INC%\[version]\km\wdf.h
echo.
echo     Install it from the Visual Studio Installer:
echo       1. Open "Visual Studio Installer"
echo       2. Visual Studio 2022 Community -^> Modify
echo       3. "Individual components" tab -^> search "Windows Driver Kit"
echo       4. Check it -^> Modify, then reboot
echo.
echo     Or:  winget install -e --id Microsoft.WindowsWDK.10.0.26100
echo     Docs: https://learn.microsoft.com/windows-hardware/drivers/download-the-wdk
echo.
exit /b 1

