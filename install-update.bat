@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "REPO_URL=https://github.com/estebanstifli/Meet2Notes.git"
set "REPO_DIR=Meet2Notes"
set "INSTALL_ROOT=%~dp0"
set "GIT_INSTALLER=%TEMP%\meet2notes-git-installer.exe"
set "PYTHON_INSTALLER=%TEMP%\meet2notes-python-installer.exe"
set "PYTHON_INSTALLER_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"

if /I "%~1"=="--help" goto :help
if not "%~1"=="" (
    echo ERROR: Unknown option: %~1
    echo Run install-update.bat --help for usage.
    exit /b 2
)

echo =====================================================
echo Meet2Notes - Windows installer and updater
echo =====================================================
echo.

REM When this file is already inside the repository, update in place.
if exist "%~dp0.git" if exist "%~dp0install.ps1" (
    set "REPOSITORY_PATH=%~dp0"
    goto :repository_ready
)

set "REPOSITORY_PATH=%INSTALL_ROOT%%REPO_DIR%"
echo Installation folder:
echo   %REPOSITORY_PATH%
echo.

call :ensure_git
if errorlevel 1 goto :failed

if exist "%REPOSITORY_PATH%" (
    if not exist "%REPOSITORY_PATH%\.git" (
        echo ERROR: The destination exists but is not a Meet2Notes Git repository.
        echo Rename or remove this folder, then run the installer again:
        echo   %REPOSITORY_PATH%
        goto :failed
    )
) else (
    echo Cloning Meet2Notes...
    git clone "%REPO_URL%" "%REPOSITORY_PATH%"
    if errorlevel 1 (
        echo ERROR: Meet2Notes could not be downloaded.
        goto :failed
    )
)

:repository_ready
call :ensure_git
if errorlevel 1 goto :failed

pushd "%REPOSITORY_PATH%"
if errorlevel 1 (
    echo ERROR: The installation folder could not be opened.
    goto :failed
)

echo Updating Meet2Notes...
git pull --ff-only
if errorlevel 1 (
    echo ERROR: The update could not be applied safely.
    echo If you edited files in this folder, preserve those changes before retrying.
    popd
    goto :failed
)

call :ensure_python
if errorlevel 1 (
    popd
    goto :failed
)

echo.
echo Installing Meet2Notes and its local AI environment...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File ".\install.ps1"
if errorlevel 1 (
    echo ERROR: Meet2Notes setup did not finish successfully.
    popd
    goto :failed
)

popd
echo.
echo =====================================================
echo Meet2Notes is ready
echo =====================================================
echo Installed in:
echo   %REPOSITORY_PATH%
echo.
echo To start the application, double-click:
echo   %REPOSITORY_PATH%\start.bat
echo.
pause
exit /b 0

:help
echo Meet2Notes Windows installer and updater
echo.
echo Usage: install-update.bat
echo.
echo When downloaded separately, this file installs Meet2Notes in a
echo "Meet2Notes" folder beside the installer. When run from inside an
echo existing Meet2Notes repository, it updates that installation in place.
exit /b 0

:ensure_git
where git >nul 2>&1
if not errorlevel 1 (
    echo Git is installed.
    exit /b 0
)

echo Git was not found. Installing Git for Windows...
where winget >nul 2>&1
if not errorlevel 1 (
    winget install --id Git.Git --exact --source winget --silent --accept-package-agreements --accept-source-agreements
)

set "PATH=%ProgramFiles%\Git\cmd;%LocalAppData%\Programs\Git\cmd;%PATH%"
where git >nul 2>&1
if not errorlevel 1 exit /b 0

echo Windows Package Manager was unavailable or did not complete. Trying the official Git release...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $headers=@{'User-Agent'='Meet2Notes-Installer'}; try { $release=Invoke-RestMethod -Uri 'https://api.github.com/repos/git-for-windows/git/releases/latest' -Headers $headers; $asset=$release.assets ^| Where-Object { $_.name -match '64-bit\.exe$' -and $_.name -notmatch 'portable^|mingit' } ^| Select-Object -First 1; if (-not $asset) { throw 'No compatible Git installer was found.' }; Invoke-WebRequest -Uri $asset.browser_download_url -OutFile '%GIT_INSTALLER%' -UseBasicParsing; if ((Get-Item '%GIT_INSTALLER%').Length -lt 1MB) { throw 'The Git download is incomplete.' }; if ((Get-AuthenticodeSignature '%GIT_INSTALLER%').Status -ne 'Valid') { throw 'The Git installer signature is not valid.' } } catch { Write-Error $_.Exception.Message; exit 1 }"
if errorlevel 1 exit /b 1

"%GIT_INSTALLER%" /VERYSILENT /NORESTART /NOCANCEL /SP-
if errorlevel 1 exit /b 1
set "PATH=%ProgramFiles%\Git\cmd;%LocalAppData%\Programs\Git\cmd;%PATH%"
where git >nul 2>&1
if errorlevel 1 (
    echo ERROR: Git could not be installed automatically.
    echo Install it from https://git-scm.com/download/win and retry.
    exit /b 1
)
exit /b 0

:ensure_python
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "$commands=@(@('py','-3.12'),@('py','-3.13'),@('py','-3.11'),@('python')); foreach($item in $commands){ try { if($item.Count -gt 1){ ^& $item[0] $item[1] -c 'import sys; raise SystemExit(0 if sys.version_info -ge (3,11) else 1)' 2^>$null } else { ^& $item[0] -c 'import sys; raise SystemExit(0 if sys.version_info -ge (3,11) else 1)' 2^>$null }; if($LASTEXITCODE -eq 0){ exit 0 } } catch {} }; exit 1"
if not errorlevel 1 (
    echo Python 3.11 or newer is installed.
    exit /b 0
)

echo Python 3.11 or newer was not found. Installing Python 3.12...
where winget >nul 2>&1
if not errorlevel 1 (
    winget install --id Python.Python.3.12 --exact --source winget --silent --scope user --accept-package-agreements --accept-source-agreements
)

set "PATH=%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts;%PATH%"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "$candidates=@('%LocalAppData%\Programs\Python\Python312\python.exe','python'); foreach($python in $candidates){ try { ^& $python -c 'import sys; raise SystemExit(0 if sys.version_info -ge (3,11) else 1)' 2^>$null; if($LASTEXITCODE -eq 0){ exit 0 } } catch {} }; exit 1"
if not errorlevel 1 exit /b 0

echo Windows Package Manager was unavailable or did not complete. Downloading Python from python.org...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri '%PYTHON_INSTALLER_URL%' -OutFile '%PYTHON_INSTALLER%' -UseBasicParsing; if ((Get-Item '%PYTHON_INSTALLER%').Length -lt 10MB) { throw 'The Python download is incomplete.' }; if ((Get-AuthenticodeSignature '%PYTHON_INSTALLER%').Status -ne 'Valid') { throw 'The Python installer signature is not valid.' } } catch { Write-Error $_.Exception.Message; exit 1 }"
if errorlevel 1 exit /b 1

"%PYTHON_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=0 Include_launcher=1 Include_test=0
if errorlevel 1 exit /b 1
set "PATH=%LocalAppData%\Programs\Python\Python312;%LocalAppData%\Programs\Python\Python312\Scripts;%PATH%"
if not exist "%LocalAppData%\Programs\Python\Python312\python.exe" (
    echo ERROR: Python could not be installed automatically.
    echo Install Python 3.12 from https://www.python.org/downloads/windows/ and retry.
    exit /b 1
)
exit /b 0

:failed
echo.
echo Installation stopped. The messages above describe what needs attention.
pause
exit /b 1
