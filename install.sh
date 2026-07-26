#!/usr/bin/env bash
set -euo pipefail

INSTALLER_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENVIRONMENT_ROOT="${INSTALLER_ROOT}/.venv"
MODELS="all"
WHISPER_MODEL="small"
DEV="false"
START="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-models) MODELS="none" ;;
    --whisper-model)
      shift
      WHISPER_MODEL="${1:?Missing Whisper model name}"
      ;;
    --dev) DEV="true" ;;
    --start) START="true" ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
  shift
done

step() {
  printf "\n\033[36m==> %s\033[0m\n" "$1"
}

find_python() {
  local candidate
  for candidate in python3.12 python3.13 python3.11 python3; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      if "${candidate}" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
        printf "%s" "${candidate}"
        return 0
      fi
    fi
  done
  return 1
}

cd "${INSTALLER_ROOT}"
printf "\033[34mMeet2Notes installer\033[0m\n"
echo "Private local transcription, diarization, and meeting summaries"

if [[ ! -x "${ENVIRONMENT_ROOT}/bin/python" ]]; then
  step "Creating the isolated Python environment"
  BOOTSTRAP_PYTHON="$(find_python || true)"
  if [[ -z "${BOOTSTRAP_PYTHON}" ]]; then
    echo "Python 3.11+ was not found: https://www.python.org/downloads/" >&2
    exit 1
  fi
  "${BOOTSTRAP_PYTHON}" -m venv "${ENVIRONMENT_ROOT}"
fi

PYTHON="${ENVIRONMENT_ROOT}/bin/python"
step "Installing Meet2Notes and native audio/AI runtimes"
"${PYTHON}" -m pip install --upgrade pip setuptools wheel
"${PYTHON}" -m pip install -e ".[capture,transcription,diarization]"
"${PYTHON}" -m pip install "huggingface-hub>=0.27,<2"

PYTHON_MINOR="$("${PYTHON}" -c 'import sys; print(sys.version_info.minor)')"
LLAMA_INDEX="https://abetlen.github.io/llama-cpp-python/whl/cpu"
if [[ "$(uname -s)" == "Darwin" ]]; then
  LLAMA_INDEX="https://abetlen.github.io/llama-cpp-python/whl/metal"
elif command -v nvidia-smi >/dev/null 2>&1 && [[ "${PYTHON_MINOR}" -le 12 ]]; then
  LLAMA_INDEX="https://abetlen.github.io/llama-cpp-python/whl/cu124"
fi
if ! "${PYTHON}" -m pip install "llama-cpp-python>=0.3.8,<1" \
  --extra-index-url "${LLAMA_INDEX}"; then
  echo "Accelerated llama.cpp wheel unavailable; using the portable CPU wheel."
  "${PYTHON}" -m pip install --force-reinstall "llama-cpp-python>=0.3.8,<1" \
    --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/cpu"
fi

if [[ "${DEV}" == "true" ]]; then
  step "Installing development tools"
  "${PYTHON}" -m pip install -e ".[dev]"
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo
  echo "FFmpeg is required for imported media."
  if [[ "$(uname -s)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
    step "Installing FFmpeg with Homebrew"
    brew install ffmpeg
  elif command -v apt-get >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
    step "Installing FFmpeg with apt"
    sudo apt-get update
    sudo apt-get install -y ffmpeg
  else
    echo "Install FFmpeg with your operating system package manager."
  fi
fi

if [[ "${MODELS}" == "all" ]]; then
  step "Downloading and verifying the recommended local AI models"
  "${PYTHON}" -m local_meeting_ai.model_setup \
    --models all \
    --whisper-model "${WHISPER_MODEL}"
fi

step "Verifying the installation"
"${PYTHON}" -m pip check
"${PYTHON}" scripts/check_environment.py

printf "\n\033[32mMeet2Notes is ready.\033[0m\n"
echo "Run: .venv/bin/meet2notes"

if [[ "${START}" == "true" ]]; then
  "${ENVIRONMENT_ROOT}/bin/meet2notes"
fi
