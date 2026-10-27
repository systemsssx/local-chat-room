@echo off
rem ============================================================
rem  Allow inbound TCP 8000 through Windows Firewall.
rem
rem  HOW TO USE: right-click this file -> "Run as administrator"
rem
rem  KEEP THIS FILE 100%% ASCII (cmd.exe re-reads .cmd files by byte
rem  offset; non-ASCII bytes break parsing, even inside "rem").
rem ============================================================
net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [!] Administrator rights required.
    echo.
    echo       RIGHT-CLICK this file and choose
    echo       "Run as administrator"
    echo.
    pause
    exit /b 1
)

netsh advfirewall firewall delete rule name="Chat Room 8000" >nul 2>&1
netsh advfirewall firewall add rule name="Chat Room 8000" dir=in action=allow protocol=TCP localport=8000 profile=any

if errorlevel 1 (
    echo.
    echo   [x] Failed to add the rule.
    echo.
) else (
    echo.
    echo   [OK] Inbound TCP 8000 is now allowed for all network profiles.
    echo        Phones / tablets on the same WiFi can reach this server.
    echo.
    echo   To remove it later, run:
    echo       netsh advfirewall firewall delete rule name="Chat Room 8000"
    echo.
)
pause
