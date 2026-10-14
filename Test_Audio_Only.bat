@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run Setup_and_Run.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -c "from speech_backend import play_test_tone; play_test_tone(); print('Beep completed')"
if errorlevel 1 pause
