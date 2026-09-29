@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Re-launch once under a console that keeps a log and never closes early.
if not "%~1"=="--inner" (
    if not exist "%~dp0build.log" echo. > "%~dp0build.log"
    echo ============================================== >> "%~dp0build.log"
    echo Build started %DATE% %TIME% >> "%~dp0build.log"
    call "%~f0" --inner 2>&1 | powershell -NoProfile -Command "$input | Tee-Object -FilePath '%~dp0build.log' -Append"
    echo.
    echo --------------------------------------------------
    echo Build finished. Full log saved to build.log
    echo If something failed, open build.log or scroll up.
    echo Press any key to close this window.
    pause >nul
    exit /b
)

REM ============================================================
REM  ValAim - full Windows build
REM
REM  [1/4] Python venv + deps
REM  [2/4] Kernel driver (vhidmouse.sys)  -- optional, skipped if no WDK
REM  [3/4] ValAim.exe (PyInstaller)
REM  [4/4] Stage everything into dist\ValAim\
REM
REM  Select ONNX Runtime backend: cpu, amd, or cuda.
REM  Do NOT install more than one onnxruntime variant at a time.
REM ============================================================
set "BACKEND=amd"

REM Kernel driver disabled: the Bluetooth (bt) backend is used instead.
set "BUILD_DRIVER=0"

cd /d "%~dp0"
if errorlevel 1 goto :error_cd

set "ROOT=%~dp0"
set "DIST=%ROOT%dist"
set "STAGE=%DIST%\ValAim"
set "LOG=%ROOT%build.log"
set "PF86=%ProgramFiles(x86)%"

echo ==== ValAim build %DATE% %TIME% ==== >> "%LOG%"

echo.
echo ================================================
echo          ValAim Windows Build
echo ================================================
echo.

REM ==================================================
REM [1/4] Python environment
REM ==================================================
echo [1/4] Preparing Python environment...

if exist ".venv\Scripts\python.exe" goto :venv_ready

echo Creating .venv...
where py >nul 2>&1
if not errorlevel 1 goto :create_with_py

where python >nul 2>&1
if not errorlevel 1 goto :create_with_python

echo [error] Python not found. Install Python 3.10+ from https://www.python.org/downloads/
pause
exit /b 1

:create_with_py
py -3 -m venv ".venv"
if errorlevel 1 goto :error_venv
goto :venv_ready

:create_with_python
python -m venv ".venv"
if errorlevel 1 goto :error_venv
goto :venv_ready

:venv_ready
set "PYEXE=%ROOT%.venv\Scripts\python.exe"
echo Using: %PYEXE%
echo.

echo Installing dependencies...
"%PYEXE%" -m pip install --upgrade pip
if errorlevel 1 goto :error_pip

set "ORT_PKG=onnxruntime"
if /i "%BACKEND%"=="amd" set "ORT_PKG=onnxruntime-directml"
if /i "%BACKEND%"=="cuda" set "ORT_PKG=onnxruntime-gpu"

echo Backend: %BACKEND%  (^> %ORT_PKG%)
"%PYEXE%" -m pip uninstall -y onnxruntime onnxruntime-directml onnxruntime-gpu >nul 2>&1
"%PYEXE%" -m pip install numpy opencv-python mss %ORT_PKG% pyinstaller
if errorlevel 1 goto :error_deps
echo.

REM ==================================================
REM [2/4] Kernel driver
REM ==================================================
echo [2/4] Building VHF virtual mouse driver...

if "%BUILD_DRIVER%"=="0" (
    echo Skipped ^(BUILD_DRIVER=0^).
    goto :driver_done
)

where msbuild >nul 2>&1
if errorlevel 1 (
    if not exist "%PF86%\Microsoft Visual Studio\Installer\vswhere.exe" (
        echo [warn] Neither msbuild nor vswhere found. Install Visual Studio 2022
        echo        ^("Desktop development with C++" workload^) plus the WDK,
        echo        or set BUILD_DRIVER=0 to skip the driver.
        goto :driver_done
    )
)

call "%ROOT%kernel\vhidmouse\build.cmd"
if errorlevel 1 (
    echo [warn] Driver build failed. The exe will still build; input falls back to SendInput.
    echo        See kernel\README.md for the WDK requirement.
    echo.
    echo        ----- driver build output above (window will stay open) -----
    pause
    goto :driver_done
)

:driver_done
echo.

REM ==================================================
REM [3/4] ValAim.exe
REM ==================================================
echo [3/4] Building ValAim.exe...
if not exist "valaim.spec" goto :error_spec
"%PYEXE%" -m PyInstaller "valaim.spec" --noconfirm --clean
if errorlevel 1 goto :error_build
echo.

REM ==================================================
REM [4/4] Stage deliverables
REM ==================================================
echo [4/4] Staging into dist\ValAim\...

if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%" 2>nul

copy /y "%DIST%\ValAim.exe" "%STAGE%\ValAim.exe" >nul
if errorlevel 1 goto :error_stage

if exist "%ROOT%kernel\vhidmouse\build\vhidmouse\vhidmouse.sys" (
    mkdir "%STAGE%\driver" 2>nul
    copy /y "%ROOT%kernel\vhidmouse\build\vhidmouse\vhidmouse.sys" "%STAGE%\driver\" >nul
    copy /y "%ROOT%kernel\vhidmouse\vhidmouse.inf" "%STAGE%\driver\" >nul
    copy /y "%ROOT%kernel\README.md" "%STAGE%\driver\README.md" >nul
    copy /y "%ROOT%install_driver.bat" "%STAGE%\install_driver.bat" >nul
    copy /y "%ROOT%uninstall_driver.bat" "%STAGE%\uninstall_driver.bat" >nul
    echo   - exe     : %STAGE%\ValAim.exe
    echo   - driver  : %STAGE%\driver\vhidmouse.sys
    echo   - installer: %STAGE%\install_driver.bat
) else (
    echo   - exe     : %STAGE%\ValAim.exe
    echo   - driver  : NOT BUILT ^(see kernel\README.md^)
)

echo.
echo ================================================
echo Build completed successfully.
echo.
echo Run:
echo   %STAGE%\ValAim.exe --debug
echo.
echo The exe defaults to --input-backend vhid. If the driver is not
echo loaded it falls back to SendInput automatically.
echo ================================================
echo.
pause
endlocal
exit /b 0


:error_cd
echo [error] Failed to enter project directory.
pause
exit /b 1

:error_venv
echo [error] Failed to create virtual environment.
pause
exit /b 1

:error_pip
echo [error] pip upgrade failed.
pause
exit /b 1

:error_deps
echo [error] Dependency installation failed.
pause
exit /b 1

:error_spec
echo [error] valaim.spec not found in %ROOT%
pause
exit /b 1

:error_build
echo [error] PyInstaller build failed.
pause
exit /b 1

:error_stage
echo [error] Could not copy dist\ValAim.exe - did PyInstaller change its output path?
pause
exit /b 1
