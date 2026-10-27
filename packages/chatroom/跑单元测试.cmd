@echo off
rem ============================================================
rem  Run the Python unit tests (no server needed, ~3 seconds).
rem
rem  KEEP THIS FILE 100%% ASCII (including comments!).
rem  cmd.exe re-reads a .cmd file by BYTE OFFSET while running; any
rem  non-ASCII byte makes it lose its place and chop the following
rem  lines into garbage commands. Chinese output comes from Python.
rem ============================================================
title Chat Room - Unit Tests
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   [x] Python not found.
    echo       Install Python 3.10+ and tick "Add python.exe to PATH"
    echo.
    pause
    exit /b 1
)

python -u -m unittest discover
echo.
echo   ------------------------------------------------------------
echo   Static frontend check (ids used by JS must exist in the pages):
echo        python dev/ui-check.py
echo   End-to-end against a running server:
echo        python dev/e2e-check.py
echo   A single test file:
echo        python -m unittest tests.test_rooms
echo   ------------------------------------------------------------
pause
