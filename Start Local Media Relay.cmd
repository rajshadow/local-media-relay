@echo off
cd /d "%~dp0"
where pythonw.exe >nul 2>&1
if errorlevel 1 (
    echo Install Python 3.11 or later with Tkinter and add it to PATH.
    pause
    exit /b 1
)
start "" pythonw.exe -B "%~dp0app.py"
