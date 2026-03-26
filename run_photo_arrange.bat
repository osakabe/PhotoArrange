@echo off
setlocal
cd /d "%~dp0"

echo [PhotoArrange Launcher]
echo Initializing environment...

:: Set current conda environment python path
set PYTHON_EXE="c:\Users\osaka\miniforge3\envs\photo_env\python.exe"

if not exist %PYTHON_EXE% (
    echo ERROR: Python interpreter not found at %PYTHON_EXE%
    echo Please check your Conda environment path.
    pause
    exit /b 1
)

echo Launching application...
%PYTHON_EXE% main.py

if %ERRORLEVEL% neq 0 (
    echo.
    echo Application exited with error code %ERRORLEVEL%.
    pause
)

endlocal
