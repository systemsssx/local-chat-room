@echo off
rem ============================================================
rem  Chat Room - double click to start the server.
rem
rem  KEEP THIS FILE 100%% ASCII (including comments!).
rem  cmd.exe re-reads a .bat/.cmd file by BYTE OFFSET while running.
rem  Any non-ASCII byte (even inside "rem") makes it lose its place
rem  and chop the following lines into garbage commands.
rem  All Chinese output is printed by Python instead (server.py sets UTF-8).
rem ============================================================
title Chat Room - Server
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

where python >nul 2>nul
if errorlevel 1 (
    echo.
    echo   [x] Python not found.
    echo       Install Python 3.10+ and tick "Add python.exe to PATH"
    echo       https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

rem -u = unbuffered, so messages show up immediately
rem --open = open the browser once the server is listening
python -u server.py --open

echo.
echo   [server stopped]
pause
