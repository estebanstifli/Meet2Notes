# Installing Meet2Notes from zero

Meet2Notes does not require an unsigned `.exe`. The source checkout contains
readable PowerShell, Batch, and Bash installers. They create an isolated Python
environment inside the project, install the native runtimes, download the
selected models, and verify the result.

## Before you start

Recommended hardware:

- 64-bit Windows 10/11, macOS, or a current 64-bit Linux distribution.
- 8 GiB RAM minimum; 16 GiB recommended for transcription plus summaries.
- About 4 GiB free for the environment, models, caches, and initial recordings.
- Python 3.12 recommended. Python 3.11 and 3.13 are supported, but current
  prebuilt llama.cpp CUDA wheels are available only for Python 3.10-3.12.
- An internet connection is needed for the initial package and model download.
  Meetings can be processed locally afterward.

This repository is currently private. The GitHub account cloning it must have
access. Git Credential Manager normally opens a browser sign-in when HTTPS
authentication is required.

## Windows 10/11

### 1. Install Git and Python

Open a normal PowerShell window. Administrator mode is usually unnecessary:

```powershell
winget install --id Git.Git --exact
winget install --id Python.Python.3.12 --exact
```

Close and reopen PowerShell so the new commands are visible:

```powershell
git --version
py -3.12 --version
```

If `winget` is unavailable, install
[Git for Windows](https://git-scm.com/download/win) and
[Python 3.12](https://www.python.org/downloads/) from their official sites.
Enable **Add Python to PATH** in the Python installer.

### 2. Clone and install

```powershell
git clone https://github.com/estebanstifli/Meet2Notes.git
cd Meet2Notes
.\install.cmd -Start
```

`install.cmd` invokes the readable `install.ps1` with a process-scoped execution
policy bypass. It does not alter the machine's permanent PowerShell policy.

If the repository login does not open automatically, GitHub CLI is an
alternative:

```powershell
winget install --id GitHub.cli --exact
gh auth login
gh repo clone estebanstifli/Meet2Notes
cd Meet2Notes
.\install.cmd -Start
```

Later launches:

```powershell
.\.venv\Scripts\meet2notes.exe
```

Useful installer choices:

```powershell
# Portable CPU installation
.\install.cmd -AiBackend cpu

# Force a CUDA llama.cpp wheel. Python 3.12 is required.
.\install.cmd -AiBackend cuda

# Install the app now but postpone the ~1.2 GiB model download.
.\install.cmd -Models none

# Replace an existing CPU/GPU llama.cpp build with the selected backend.
.\install.cmd -AiBackend cuda -ReinstallAiRuntime

# Development tools plus immediate launch.
.\install.cmd -Dev -Start
```

The default `-AiBackend auto` uses a compatible CUDA wheel when an NVIDIA GPU
and Python 3.10-3.12 are available; otherwise it safely uses CPU.

## macOS

These instructions support Apple Silicon and Intel Macs.

### 1. Install the command-line prerequisites

Install Homebrew if necessary:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Then install Git, Python, FFmpeg, and PortAudio:

```bash
brew install git python@3.12 ffmpeg portaudio
git --version
python3.12 --version
ffmpeg -version
```

### 2. Clone and install

```bash
git clone https://github.com/estebanstifli/Meet2Notes.git
cd Meet2Notes
chmod +x install.sh
./install.sh --start
```

The default installer requests a Metal llama.cpp wheel and falls back to CPU if
that wheel is unavailable for the Mac or Python version.

Later launches:

```bash
.venv/bin/meet2notes
```

To force or replace a backend:

```bash
./install.sh --ai-backend cpu
./install.sh --ai-backend metal --reinstall-ai-runtime
```

## Ubuntu or Debian

### 1. Install native prerequisites

```bash
sudo apt update
sudo apt install -y \
  git python3 python3-venv python3-dev \
  ffmpeg portaudio19-dev libsndfile1 \
  build-essential cmake
```

Python 3.12 is preferred when your distribution provides it. Python 3.11 or
3.13 can also run Meet2Notes.

### 2. Clone and install

```bash
git clone https://github.com/estebanstifli/Meet2Notes.git
cd Meet2Notes
chmod +x install.sh
./install.sh --start
```

For NVIDIA CUDA with a compatible driver and Python 3.10-3.12:

```bash
nvidia-smi
./install.sh --ai-backend cuda --reinstall-ai-runtime
```

The CUDA toolkit is not required for the prebuilt wheel, although the installed
NVIDIA driver must support its CUDA runtime. If installation fails in automatic
mode, the script falls back to CPU.

## Fedora

Install the prerequisites using the package repositories configured on the
machine:

```bash
sudo dnf install -y \
  git python3 python3-devel \
  ffmpeg portaudio-devel libsndfile \
  gcc-c++ cmake
```

Then:

```bash
git clone https://github.com/estebanstifli/Meet2Notes.git
cd Meet2Notes
chmod +x install.sh
./install.sh --start
```

Some Fedora installations require RPM Fusion before the full FFmpeg package is
available.

## Arch Linux

```bash
sudo pacman -S --needed \
  git python ffmpeg portaudio libsndfile base-devel cmake
git clone https://github.com/estebanstifli/Meet2Notes.git
cd Meet2Notes
chmod +x install.sh
./install.sh --start
```

## What the installer changes

The installers:

1. Reuse or create `.venv` inside the cloned project.
2. Install Python packages only in that environment.
3. Detect the operating system and a compatible llama.cpp backend.
4. Reuse FFmpeg/FFprobe from `PATH`, or try the normal package manager when
   either tool is missing.
5. Download and validate the selected AI models in the private application data
   directory.
6. Run `pip check` and print an environment report.

They do not install a background service, change the permanent PowerShell
execution policy, upload meeting data, or copy models into the Git repository.

## Where models come from

Model weights are not stored in the Meet2Notes repository or GitHub release.
They are downloaded directly from their upstream publishers:

| Purpose | Default files | Upstream |
|---|---|---|
| Transcription | Faster Whisper `small` | `Systran/faster-whisper-small` on Hugging Face |
| Speaker segmentation | Pyannote 3.0 ONNX INT8 | `k2-fsa/sherpa-onnx` GitHub Releases |
| Speaker embeddings | 3D-Speaker ONNX | `k2-fsa/sherpa-onnx` GitHub Releases |
| Summaries | LFM2.5 1.2B Q4_K_M GGUF | `LiquidAI/LFM2.5-1.2B-Instruct-GGUF` on Hugging Face |

The `.gitignore` explicitly rejects `.gguf`, `.onnx`, common media files,
databases, model directories, logs, and `.env` files.

Default model locations:

- Windows: `%LOCALAPPDATA%\Meet2Notes\models`
- macOS: `~/Library/Application Support/Meet2Notes/models`
- Linux: `~/.local/share/Meet2Notes/models`

Existing LocalMeet2Resume installations continue using the historical data
directory automatically so database recording paths do not break.

## What happens when something already exists

| Existing component | Installer behavior |
|---|---|
| `.venv` | Reused. Required package versions are checked and installed only when needed. |
| Python packages | Reused when they satisfy the declared version range; global Python packages are untouched. |
| FFmpeg and FFprobe | The first pair found on `PATH` is reused and its path/version is reported. |
| Only FFmpeg or only FFprobe | Treated as incomplete; the installer tries to install the complete FFmpeg distribution. |
| Whisper model | Hugging Face's local cache is reused; missing files are downloaded. |
| sherpa-onnx model pair | Existing required ONNX files are reused and loaded to verify them. |
| LFM2.5 GGUF | The existing file is reused and loaded to verify it. |
| Interrupted download | Temporary/partial data is not treated as an installed model; the next run retries or resumes through the upstream downloader. |
| Existing llama.cpp runtime | Reused by default. Use `-ReinstallAiRuntime` or `--reinstall-ai-runtime` only when changing CPU/GPU backend. |
| Existing Meet2Notes database | Preserved. Installers never delete meetings, recordings, settings, or models. |

Models installed elsewhere on the computer are not assumed to be compatible and
are not scanned automatically. Whisper and diarization use Meet2Notes' managed
model directory. A compatible GGUF stored elsewhere can be selected explicitly
with **Local model path** in Settings.

If a managed model file exists but is corrupt, model verification stops with an
error instead of silently overwriting user data. Remove only that failed model
from the model directory and rerun `meet2notes-models` to download a clean copy.

If several FFmpeg installations exist, command lookup order (`PATH`) decides
which one is used. You can select a specific executable at runtime:

```powershell
meet2notes --ffmpeg-path "C:\Tools\ffmpeg\bin\ffmpeg.exe"
```

```bash
meet2notes --ffmpeg-path /opt/ffmpeg/bin/ffmpeg
```

FFprobe must be next to that custom FFmpeg executable with the matching platform
suffix.

## Updating an existing checkout

Stop Meet2Notes, then:

```bash
git pull --ff-only
```

Windows:

```powershell
.\install.cmd
```

macOS/Linux:

```bash
./install.sh
```

The same environment and model cache are reused. This makes upgrades much
smaller than first installation.

## Troubleshooting

### The repository is private

Run `gh auth login`, verify that the account has access, and clone using
`gh repo clone estebanstifli/Meet2Notes`.

### PowerShell blocks the script

Use `install.cmd`. Its bypass applies only to that installer process. Do not
change the machine-wide execution policy.

### `python -m venv` is unavailable on Linux

Install the distribution's `python3-venv` package, remove only the incomplete
`.venv` directory, and rerun `./install.sh`.

### FFmpeg was installed but is not detected

Open a new terminal so the updated `PATH` is loaded. Check both:

```bash
ffmpeg -version
ffprobe -version
```

### A different AI backend is wanted

The installer intentionally keeps an already working llama.cpp package. Request
replacement explicitly:

```powershell
.\install.cmd -AiBackend cuda -ReinstallAiRuntime
```

```bash
./install.sh --ai-backend cuda --reinstall-ai-runtime
```
