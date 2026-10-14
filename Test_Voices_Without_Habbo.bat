@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run Setup_and_Run.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" test_local_audio.py
pause
