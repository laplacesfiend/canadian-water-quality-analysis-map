@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    python generate_map.py
    goto :end
)

where py >nul 2>nul
if %errorlevel%==0 (
    py generate_map.py
    goto :end
)

echo Python was not found. Install it from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH" in the installer), then run this again.

:end
echo.
echo Server stopped. Press any key to close this window.
pause >nul
