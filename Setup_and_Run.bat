@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Habbo Chat Reader v8.2 - Local Voices + Updater Setup
echo =======================================================
echo           HABBO CHAT READER v8.2 - SETUP
echo             100%% LOCAL AI VOICES
echo =======================================================
echo.
REM Kokoro 0.6.1 supports Python 3.10 - 3.13, not 3.14.
set "PYRUN="
where py >nul 2>&1
if not errorlevel 1 (
    py -3.12 -c "import sys" >nul 2>&1 && set "PYRUN=py -3.12"
    if not defined PYRUN py -3.11 -c "import sys" >nul 2>&1 && set "PYRUN=py -3.11"
    if not defined PYRUN py -3.13 -c "import sys" >nul 2>&1 && set "PYRUN=py -3.13"
    if not defined PYRUN py -3.10 -c "import sys" >nul 2>&1 && set "PYRUN=py -3.10"
)
if not defined PYRUN (
    REM Python may be on PATH without the py launcher.
    where python >nul 2>&1
    if not errorlevel 1 (
        python -c "import sys; assert (3,10) <= sys.version_info[:2] < (3,14)" >nul 2>&1 && set "PYRUN=python"
    )
)
if not defined PYRUN (
    echo A compatible Python version was not found.
    echo Kokoro requires Python 3.10 through 3.13, not Python 3.14.
    echo Install Python 3.12 from https://www.python.org/downloads/release/python-31210/
    echo Choose Windows installer 64-bit. You may keep your other Python versions.
    echo Then run this file again.
    goto :failure
)
echo Using %PYRUN%
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating a private Python environment...
    %PYRUN% -m venv ".venv"
    if errorlevel 1 goto :failure
)
echo [2/3] Installing offline voice and OCR packages...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :failure

echo [3/3] Checking Tesseract OCR...
where tesseract >nul 2>&1
if not errorlevel 1 goto :run
if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" goto :run
if exist "%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe" goto :run
where winget >nul 2>&1
if errorlevel 1 (
    echo Tesseract OCR is not installed. Download from:
    echo https://github.com/UB-Mannheim/tesseract/wiki
) else (
    echo Installing Tesseract OCR using winget...
    winget install --id UB-Mannheim.TesseractOCR -e --accept-package-agreements --accept-source-agreements
    if errorlevel 1 echo Install Tesseract manually if Preview bubbles reports it missing.
)
:run
echo.
echo Launching Habbo Chat Reader v8.2...
echo In the app: click "Download local voices" one time.
echo Model download only needs internet during first-time setup.
".venv\Scripts\python.exe" habbo_reader.py
if errorlevel 1 goto :failure
exit /b 0
:failure
echo.
echo Setup or launch failed. Read the error above.
pause
exit /b 1
