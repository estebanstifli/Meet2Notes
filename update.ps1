[CmdletBinding()]
param(
    [switch]$Restart
)

$ErrorActionPreference = "Stop"
$RepositoryRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $RepositoryRoot ".venv\Scripts\python.exe"
$RequestFile = Join-Path $RepositoryRoot ".meet2notes-update-request.json"
$ExpectedRemote = "https://github.com/estebanstifli/Meet2Notes.git"
$InstallExtras = ".[capture,transcription,diarization,nvidia-asr,pyannote-diarization]"

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

function Quote-ProcessArgument {
    param([string]$Value)
    return '"' + $Value.Replace('"', '\"') + '"'
}

Set-Location -LiteralPath $RepositoryRoot
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Meet2Notes is not installed. Run install-update.bat first."
}
if (-not (Test-Path -LiteralPath $RequestFile)) {
    throw "No prepared update was found. Run update.bat first."
}

$Request = Get-Content -LiteralPath $RequestFile -Raw | ConvertFrom-Json
if ($Request.repository -ne "estebanstifli/Meet2Notes") {
    throw "The update request targets an unexpected repository."
}
if ($Request.tag -notmatch '^v?\d+\.\d+\.\d+$') {
    throw "The update request contains an invalid release tag."
}

$Remote = (& git remote get-url origin).Trim()
if ($LASTEXITCODE -ne 0 -or $Remote -ne $ExpectedRemote) {
    throw "Updates are only accepted from $ExpectedRemote."
}
$Changes = & git status --porcelain
if ($LASTEXITCODE -ne 0) {
    throw "The Git working tree could not be inspected."
}
if ($Changes) {
    throw "Local source changes were found. Preserve or commit them before updating."
}

Write-Host ""
Write-Host "Backing up Meet2Notes data..." -ForegroundColor Cyan
Invoke-Checked $Python @("-m", "local_meeting_ai.updater", "backup", "--request-file", $RequestFile)
$Request = Get-Content -LiteralPath $RequestFile -Raw | ConvertFrom-Json
$BackupPath = $Request.database_backup
$OldHead = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "The installed Git revision could not be read."
}

Write-Host "Downloading verified release $($Request.tag)..." -ForegroundColor Cyan
Invoke-Checked "git" @(
    "fetch", "--force", "origin",
    "refs/tags/$($Request.tag):refs/tags/$($Request.tag)"
)
$TargetCommit = (& git rev-parse "$($Request.tag)^{commit}").Trim()
if ($LASTEXITCODE -ne 0) {
    throw "The release tag could not be resolved."
}
& git merge-base --is-ancestor $OldHead $TargetCommit
if ($LASTEXITCODE -ne 0) {
    throw "The release is not a safe fast-forward from this installation."
}

try {
    Invoke-Checked "git" @("merge", "--ff-only", $TargetCommit)
    Write-Host "Updating Python dependencies without replacing local models..." -ForegroundColor Cyan
    Invoke-Checked $Python @(
        "-m", "pip", "install", "--disable-pip-version-check", "-e", $InstallExtras
    )
    Invoke-Checked $Python @("-m", "pip", "check")
    Write-Host "Testing database migrations on the backup copy..." -ForegroundColor Cyan
    Invoke-Checked $Python @(
        "-m", "local_meeting_ai.updater", "validate", "--request-file", $RequestFile
    )
} catch {
    Write-Warning "The update did not validate. Restoring the previous source revision."
    & git reset --hard $OldHead
    throw
}

Remove-Item -LiteralPath $RequestFile -Force
Write-Host ""
Write-Host "Meet2Notes was updated to $($Request.target_version)." -ForegroundColor Green
if ($BackupPath) {
    Write-Host "Pre-update database backup: $BackupPath"
}

if ($Restart) {
    $Arguments = @($Request.app_args | ForEach-Object { Quote-ProcessArgument ([string]$_) })
    $env:M2N_SKIP_UPDATE_CHECK = "1"
    try {
        $StartParameters = @{
            FilePath = Join-Path $RepositoryRoot "start.bat"
            WorkingDirectory = $RepositoryRoot
        }
        if ($Arguments.Count -gt 0) {
            $StartParameters.ArgumentList = $Arguments -join " "
        }
        Start-Process @StartParameters
    } finally {
        Remove-Item Env:M2N_SKIP_UPDATE_CHECK -ErrorAction SilentlyContinue
    }
}
