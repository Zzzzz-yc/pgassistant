@echo off
setlocal EnableExtensions
rem pgassistant one-click launcher for Windows.
rem
rem Why this file exists: start.ps1 is an ordinary project script with no
rem Authenticode signature. On machines whose execution policy is AllSigned
rem (or RemoteSigned with a downloaded, mark-of-the-web start.ps1) PowerShell
rem refuses to run it and reports that the file is not digitally signed.
rem This launcher starts the same script with -ExecutionPolicy Bypass for
rem this one process only, so nothing system-wide is changed and no
rem certificate has to be installed.
rem
rem Args are forwarded unchanged, so these all work:
rem   start.cmd
rem   start.cmd -NoBrowser
rem   start.cmd -Dev

set "PS1=%~dp0start.ps1"
if not exist "%PS1%" (
    echo [start.cmd] start.ps1 was not found next to this launcher.
    pause
    exit /b 1
)

powershell.exe -NoProfile -NoLogo -ExecutionPolicy Bypass -File "%PS1%" %*
set "RC=%errorlevel%"

if not "%RC%"=="0" (
    echo.
    echo [start.cmd] start.ps1 exited with code %RC%. Read the error above.
    pause
)

endlocal & exit /b %RC%
