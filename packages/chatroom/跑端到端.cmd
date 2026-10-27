@echo off
rem ============================================================
rem  End-to-end check against a RUNNING server (pure HTTP, no browser).
rem  Start the server first (启动服务.cmd), then double click this file.
rem
rem  KEEP THIS FILE 100%% ASCII (including comments!).
rem ============================================================
title Chat Room - End-to-End Check
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

python -u dev\e2e-check.py
echo.
pause
