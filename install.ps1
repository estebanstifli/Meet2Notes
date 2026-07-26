[CmdletBinding()]
param(
    [ValidateSet("auto", "cpu", "cuda")]
    [string]$AiBackend = "auto",

    [ValidateSet("all", "none")]
    [string]$Models = "all",

    [ValidateSet(
        "tiny",
        "base",
        "small",
        "medium",
        "large-v3",
        "distil-large-v3",
        "turbo"
    )]
    [string]$WhisperModel = "small",

    [switch]$SkipFfmpeg,
    [switch]$ReinstallAiRuntime,
    [switch]$Dev,
    [switch]$Start
)

$ErrorActionPreference = "Stop"
$InstallerRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvironmentRoot = Join-Path $InstallerRoot ".venv"
$EnvironmentPython = Join-Path $EnvironmentRoot "Scripts\python.exe"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-Checked {
    param(
        [string]$Command,
        [string[]]$Arguments
    )
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed ($LASTEXITCODE): $Command $($Arguments -join ' ')"
    }
}

function Get-BootstrapPython {
    $Launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($Launcher) {
        foreach ($Version in @("-3.12", "-3.13", "-3.11")) {
            & $Launcher.Source $Version -c "import sys; print(sys.executable)" *> $null
            if ($LASTEXITCODE -eq 0) {
                return @($Launcher.Source, $Version)
            }
        }
    }

    $Python = Get-Command python -ErrorAction SilentlyContinue
    if ($Python) {
        & $Python.Source -c "import sys; raise SystemExit(sys.version_info < (3, 11))" *> $null
        if ($LASTEXITCODE -eq 0) {
            return @($Python.Source)
        }
    }

    $Winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($Winget) {
        Write-Step "Python 3.12 was not found; installing it with winget"
        & $Winget.Source install --id Python.Python.3.12 --exact --source winget `
            --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -eq 0) {
            $InstalledPython = Join-Path $env:LOCALAPPDATA `
                "Programs\Python\Python312\python.exe"
            if (Test-Path -LiteralPath $InstalledPython) {
                return @($InstalledPython)
            }
            $RefreshedLauncher = Get-Command py -ErrorAction SilentlyContinue
            if ($RefreshedLauncher) {
                return @($RefreshedLauncher.Source, "-3.12")
            }
        }
    }
    throw (
        "Python 3.11+ was not found. Install Python 3.12 from " +
        "https://www.python.org/downloads/ and run install.cmd again."
    )
}

function Install-Ffmpeg {
    $Ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
    $Ffprobe = Get-Command ffprobe -ErrorAction SilentlyContinue
    if ($Ffmpeg -and $Ffprobe) {
        $FfmpegOutput = & $Ffmpeg.Source -version 2>$null
        $FfmpegWorks = $LASTEXITCODE -eq 0
        $FfprobeOutput = & $Ffprobe.Source -version 2>$null
        $FfprobeWorks = $LASTEXITCODE -eq 0
        if ($FfmpegWorks -and $FfprobeWorks) {
            $FfmpegVersion = $FfmpegOutput | Select-Object -First 1
            $FfprobeVersion = $FfprobeOutput | Select-Object -First 1
            Write-Host "FFmpeg is already available: $FfmpegVersion"
            Write-Host "FFprobe is already available: $FfprobeVersion"
            Write-Host "Using: $($Ffmpeg.Source)"
            return
        }
        Write-Warning "FFmpeg/FFprobe were found but did not pass a version check."
    }
    if ($SkipFfmpeg) {
        Write-Warning (
            "FFmpeg and FFprobe were not both found. Media import will " +
            "require them later."
        )
        return
    }

    $Winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $Winget) {
        Write-Warning "FFmpeg was not found and winget is unavailable. Install FFmpeg manually."
        return
    }
    Write-Step "FFmpeg/FFprobe were not both found; installing FFmpeg with winget"
    & $Winget.Source install --id Gyan.FFmpeg --exact --source winget `
        --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "FFmpeg installation did not complete. Meet2Notes itself is installed."
    } else {
        Write-Host (
            "FFmpeg was installed. If this terminal cannot see it yet, open " +
            "a new terminal before starting Meet2Notes."
        )
    }
}

Set-Location -LiteralPath $InstallerRoot
Write-Host "Meet2Notes installer" -ForegroundColor Blue
Write-Host "Private local transcription, diarization, and meeting summaries"

if (-not (Test-Path -LiteralPath $EnvironmentPython)) {
    Write-Step "Creating the isolated Python environment"
    $Bootstrap = Get-BootstrapPython
    $BootstrapCommand = $Bootstrap[0]
    $BootstrapArguments = @()
    if ($Bootstrap.Count -gt 1) {
        $BootstrapArguments += $Bootstrap[1]
    }
    $BootstrapArguments += @("-m", "venv", $EnvironmentRoot)
    Invoke-Checked $BootstrapCommand $BootstrapArguments
}

$PythonVersion = & $EnvironmentPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) {
    throw "The Meet2Notes virtual environment is not usable."
}
$VersionParts = $PythonVersion.Trim().Split(".")
if ([int]$VersionParts[0] -lt 3 -or (
    [int]$VersionParts[0] -eq 3 -and [int]$VersionParts[1] -lt 11
)) {
    throw "Meet2Notes requires Python 3.11 or newer."
}

Write-Step "Installing Meet2Notes and native audio/AI runtimes"
Invoke-Checked $EnvironmentPython @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel")
Invoke-Checked $EnvironmentPython @(
    "-m", "pip", "install", "-e", ".[capture,transcription,diarization]"
)
Invoke-Checked $EnvironmentPython @(
    "-m", "pip", "install", "huggingface-hub>=0.27,<2"
)

$ResolvedBackend = $AiBackend
$NvidiaAvailable = [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue)
$CudaWheelCompatible = (
    [int]$VersionParts[0] -eq 3 -and
    [int]$VersionParts[1] -ge 10 -and
    [int]$VersionParts[1] -le 12
)
if ($ResolvedBackend -eq "auto") {
    $ResolvedBackend = if ($NvidiaAvailable -and $CudaWheelCompatible) {
        "cuda"
    } else {
        "cpu"
    }
}
if ($ResolvedBackend -eq "cuda" -and -not $CudaWheelCompatible) {
    if ($AiBackend -eq "cuda") {
        throw "The prebuilt llama.cpp CUDA wheel requires Python 3.10-3.12."
    }
    $ResolvedBackend = "cpu"
}

$LlamaIndex = if ($ResolvedBackend -eq "cuda") {
    "https://abetlen.github.io/llama-cpp-python/whl/cu124"
} else {
    "https://abetlen.github.io/llama-cpp-python/whl/cpu"
}
Write-Host "llama.cpp backend: $ResolvedBackend"
$LlamaArguments = @(
    "-m", "pip", "install",
    "llama-cpp-python>=0.3.8,<1",
    "--extra-index-url", $LlamaIndex
)
if ($ReinstallAiRuntime) {
    $LlamaArguments += @("--force-reinstall", "--no-cache-dir")
}
& $EnvironmentPython @LlamaArguments
if ($LASTEXITCODE -ne 0 -and $AiBackend -eq "auto" -and $ResolvedBackend -eq "cuda") {
    Write-Warning "CUDA wheel installation failed; falling back to the portable CPU wheel."
    Invoke-Checked $EnvironmentPython @(
        "-m", "pip", "install", "--force-reinstall",
        "llama-cpp-python>=0.3.8,<1",
        "--extra-index-url",
        "https://abetlen.github.io/llama-cpp-python/whl/cpu"
    )
} elseif ($LASTEXITCODE -ne 0) {
    throw "llama-cpp-python could not be installed for backend '$ResolvedBackend'."
}

if ($Dev) {
    Write-Step "Installing development tools"
    Invoke-Checked $EnvironmentPython @("-m", "pip", "install", "-e", ".[dev]")
}

Install-Ffmpeg

if ($Models -eq "all") {
    Write-Step "Downloading and verifying the recommended local AI models"
    Invoke-Checked $EnvironmentPython @(
        "-m", "local_meeting_ai.model_setup",
        "--models", "all",
        "--whisper-model", $WhisperModel
    )
}

Write-Step "Verifying the installation"
Invoke-Checked $EnvironmentPython @("-m", "pip", "check")
Invoke-Checked $EnvironmentPython @("scripts/check_environment.py")

Write-Host ""
Write-Host "Meet2Notes is ready." -ForegroundColor Green
Write-Host "Run: .\.venv\Scripts\meet2notes.exe"
Write-Host "Re-running install.cmd safely reuses the environment and downloaded models."

if ($Start) {
    & (Join-Path $EnvironmentRoot "Scripts\meet2notes.exe")
}
