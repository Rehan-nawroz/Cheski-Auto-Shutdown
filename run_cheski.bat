@echo off
REM Double-click launcher for Cheski Auto Shutdown.
REM Any extra arguments are passed straight through, e.g.
REM     run_cheski.bat --dry-run
setlocal
cd /d "%~dp0"
python -m cheski %*
if errorlevel 1 (
    echo.
    echo Cheski Auto Shutdown exited with an error. Press any key to close.
    pause >nul
)
endlocal
