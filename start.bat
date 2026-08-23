@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"
set "MEET2NOTES_PYTHON=%CD%\.venv\Scripts\python.exe"

if not exist "%MEET2NOTES_PYTHON%" (
    echo.
    echo Meet2Notes is not installed yet.
    echo Run install.ps1 first from PowerShell:
    echo   Set-ExecutionPolicy -Scope Process Bypass
    echo   .\install.ps1
    echo.
    pause
    exit /b 1
)

echo.
echo Meet2Notes local server
echo The browser will not open automatically.
echo The exact local address will be shown below when the server is ready.
echo Press Ctrl+C in this window to stop Meet2Notes.
echo.

if /I not "%M2N_SKIP_UPDATE_CHECK%"=="1" (
    "%MEET2NOTES_PYTHON%" -m local_meeting_ai.updater check --interactive -- %*
    set "MEET2NOTES_UPDATE_CHECK_CODE=!ERRORLEVEL!"
    if "!MEET2NOTES_UPDATE_CHECK_CODE!"=="10" (
        echo Starting the safe updater in a separate window...
        start "Meet2Notes Update" cmd.exe /c ""%CD%\update.bat" --prepared --restart"
        exit /b 0
    )
)

"%MEET2NOTES_PYTHON%" -m local_meeting_ai --no-browser %*
set "MEET2NOTES_EXIT_CODE=%ERRORLEVEL%"

if not "%MEET2NOTES_EXIT_CODE%"=="0" (
    echo.
    echo Meet2Notes stopped with exit code %MEET2NOTES_EXIT_CODE%.
)

echo.
echo Meet2Notes has stopped. This window will stay open so you can read the log.
pause

exit /b %MEET2NOTES_EXIT_CODE%
