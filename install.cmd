@echo off
setlocal

where powershell.exe >nul 2>nul
if errorlevel 1 (
    echo Meet2Notes requires Windows PowerShell 5.1 or PowerShell 7.
    exit /b 1
)

echo Starting the Meet2Notes installer...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass ^
    -File "%~dp0install.ps1" %*
set "MEET2NOTES_EXIT=%ERRORLEVEL%"

if not "%MEET2NOTES_EXIT%"=="0" (
    echo.
    echo Meet2Notes installation failed with exit code %MEET2NOTES_EXIT%.
)

exit /b %MEET2NOTES_EXIT%
