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
REM  ValAim - Windows build
REM
REM  [1/3] Python venv + deps
REM  [2/3] ValAim.exe (PyInstaller)
REM  [3/3] Stage into dist\ValAim\
REM
REM  ONNX Runtime backend: cpu, amd, or cuda.
REM  Do NOT install more than one onnxruntime variant at a time.
REM ============================================================
set "BACKEND=amd"

cd /d "%~dp0"
if errorlevel 1 goto :error_cd

set "ROOT=%~dp0"
set "DIST=%ROOT%dist"
set "STAGE=%DIST%\ValAim"

echo.
echo ================================================
echo          ValAim Windows Build
echo ================================================
echo.

REM ==================================================
REM [1/3] Python environment
REM ==================================================
echo [1/3] Preparing Python environment...

if exist ".venv\Scripts\python.exe" goto :venv_ready

echo Creating .venv...
where py >nul 2>&1
if not errorlevel 1 goto :create_with_py
where python >nul 2>&1
if not errorlevel 1 goto :create_with_python
echo [error] Python not found. Install Python 3.12 from https://www.python.org/downloads/
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
REM [2/3] ValAim.exe
REM ==================================================
echo [2/3] Building ValAim.exe...
if not exist "valaim.spec" goto :error_spec
"%PYEXE%" -m PyInstaller "valaim.spec" --noconfirm --clean
if errorlevel 1 goto :error_build
echo.

REM ==================================================
REM [3/3] Stage
REM ==================================================
echo [3/3] Staging into dist\ValAim\...
if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%" 2>nul
copy /y "%DIST%\ValAim.exe" "%STAGE%\ValAim.exe" >nul
if errorlevel 1 goto :error_stage
echo   - exe : %STAGE%\ValAim.exe

echo.
echo ================================================
echo Build completed successfully.
echo.
echo Run (Bluetooth phone input):
echo   %STAGE%\ValAim.exe --input-backend bt --bt-host YOUR_PHONE_IP
echo.
echo   Add --bt-test to draw circles and verify the link.
echo   Add --debug for the detection preview window.
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
echo [error] valaim.spec not found.
pause
exit /b 1

:error_build
echo [error] PyInstaller build failed.
pause
exit /b 1

:error_stage
echo [error] Could not copy dist\ValAim.exe.
pause
exit /b 1
