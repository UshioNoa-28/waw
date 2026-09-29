@echo off
rem ---------------------------------------------------------------------------
rem build_apk.bat - build the BtAimBridge APK without opening Android Studio.
rem
rem Requirements:
rem   * JDK 17
rem   * Android SDK (set ANDROID_HOME or ANDROID_SDK_ROOT, or edit below)
rem   * Gradle 8.9 on PATH, OR Android Studio's bundled Gradle
rem
rem Output: app\build\outputs\apk\debug\app-debug.apk
rem ---------------------------------------------------------------------------
setlocal EnableExtensions

cd /d "%~dp0"

if "%ANDROID_HOME%"=="" if "%ANDROID_SDK_ROOT%"=="" (
    if exist "%LOCALAPPDATA%\Android\Sdk" (
        set "ANDROID_HOME=%LOCALAPPDATA%\Android\Sdk"
    ) else (
        echo [!] Android SDK not found. Set ANDROID_HOME or install Android Studio.
        echo     Continuing anyway; the build will fail if the SDK is missing.
    )
)
if not "%ANDROID_HOME%"=="" set "ANDROID_SDK_ROOT=%ANDROID_HOME%"

echo [*] ANDROID_HOME=%ANDROID_HOME%

echo android.sdk.dir=%ANDROID_HOME:\=/%> local.properties
echo sdk.dir=%ANDROID_HOME:\=/%>> local.properties

where gradle >nul 2>&1
if errorlevel 1 (
    echo [!] gradle not found on PATH.
    echo     Either install Gradle 8.9, or open this folder in Android Studio
    echo     and use Build -^> Build Bundle^(s^)/APK^(s^).
    pause
    exit /b 1
)

echo [*] Building debug APK...
call gradle :app:assembleDebug
if errorlevel 1 (
    echo [!] Build failed.
    pause
    exit /b 1
)

echo.
echo [*] Done: app\build\outputs\apk\debug\app-debug.apk
echo     Copy it to the phone and install (allow "unknown sources").
echo.
pause
endlocal
exit /b 0
