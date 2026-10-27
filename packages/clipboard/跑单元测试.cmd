@echo off
rem ============================================================
rem  Run the Python unit test suite (no server needed, ~1 second).
rem
rem  KEEP THIS FILE 100%% ASCII (including comments!).
rem  cmd.exe re-reads a .cmd file by BYTE OFFSET while running; any
rem  non-ASCII byte makes it lose its place and chop the following
rem  lines into garbage commands. Chinese output comes from Python.
rem ============================================================
title Collab Clipboard - Unit Tests
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   [x] Python not found.
    echo       Install Python 3.10+ and tick "Add python.exe to PATH"
    echo.
    pause
    exit /b 1
)

python -u -m unittest discover -v
echo.
echo   ------------------------------------------------------------
echo   Tip: run a single file with, for example:
echo        python -m unittest tests.test_rooms
echo   ------------------------------------------------------------
pause
