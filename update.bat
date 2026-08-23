@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
set "MEET2NOTES_PYTHON=%CD%\.venv\Scripts\python.exe"
set "MEET2NOTES_PREPARED=0"
set "MEET2NOTES_RESTART=0"

:parse_options
if /I "%~1"=="--prepared" (
    set "MEET2NOTES_PREPARED=1"
    shift
    goto :parse_options
)
if /I "%~1"=="--restart" (
    set "MEET2NOTES_RESTART=1"
    shift
    goto :parse_options
)
if not "%~1"=="" (
    echo ERROR: Unknown option: %~1
    exit /b 2
)

if not exist "%MEET2NOTES_PYTHON%" (
    echo ERROR: Meet2Notes is not installed. Run install-update.bat first.
    pause
    exit /b 1
)

if "%MEET2NOTES_PREPARED%"=="0" (
    "%MEET2NOTES_PYTHON%" -m local_meeting_ai.updater prepare --interactive --force
    set "MEET2NOTES_CHECK_CODE=!ERRORLEVEL!"
    if "!MEET2NOTES_CHECK_CODE!"=="0" (
        pause
        exit /b 0
    )
    if not "!MEET2NOTES_CHECK_CODE!"=="10" (
        echo.
        echo The update could not be prepared.
        pause
        exit /b !MEET2NOTES_CHECK_CODE!
    )
)

set "MEET2NOTES_RESTART_SWITCH="
if "%MEET2NOTES_RESTART%"=="1" set "MEET2NOTES_RESTART_SWITCH=-Restart"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File ".\update.ps1" %MEET2NOTES_RESTART_SWITCH%
set "MEET2NOTES_UPDATE_CODE=%ERRORLEVEL%"

if not "%MEET2NOTES_UPDATE_CODE%"=="0" (
    echo.
    echo Meet2Notes was not updated. Existing data and settings were not modified.
    pause
    exit /b %MEET2NOTES_UPDATE_CODE%
)

if "%MEET2NOTES_RESTART%"=="0" pause
exit /b 0
