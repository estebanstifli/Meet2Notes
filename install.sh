#!/usr/bin/env bash
set -euo pipefail

INSTALLER_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENVIRONMENT_ROOT="${INSTALLER_ROOT}/.venv"
MODELS="all"
WHISPER_MODEL="small"
MODELS_DIRECTORY=""
DEV="false"
START="false"
AI_BACKEND="auto"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-models) MODELS="none" ;;
    --whisper-model)
      shift
      WHISPER_MODEL="${1:?Missing Whisper model name}"
      ;;
    --models-dir)
      shift
      MODELS_DIRECTORY="${1:?Missing models directory}"
      ;;
    --ai-backend)
      shift
      AI_BACKEND="${1:?Missing AI backend}"
      if [[ "${AI_BACKEND}" != "auto" && "${AI_BACKEND}" != "cpu" && "${AI_BACKEND}" != "cuda" ]]; then
        echo "--ai-backend must be auto, cpu, or cuda" >&2
        exit 2
      fi
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

PYTHON_MINOR="$("${PYTHON}" -c 'import sys; print(sys.version_info.minor)')"
NVIDIA_AVAILABLE="false"
if command -v nvidia-smi >/dev/null 2>&1; then
  NVIDIA_AVAILABLE="true"
fi

RESOLVED_BACKEND="${AI_BACKEND}"
if [[ "${RESOLVED_BACKEND}" == "auto" ]]; then
  if [[ "${NVIDIA_AVAILABLE}" == "true" ]]; then
    RESOLVED_BACKEND="cuda"
  else
    RESOLVED_BACKEND="cpu"
  fi
fi
if [[ "${RESOLVED_BACKEND}" == "cuda" && "${NVIDIA_AVAILABLE}" != "true" ]]; then
  echo "No NVIDIA driver was detected. Install the driver or use --ai-backend cpu." >&2
  exit 1
fi

if [[ "${RESOLVED_BACKEND}" == "cuda" ]]; then
  TORCH_VERSION="2.13.0+cu126"
  TORCH_INDEX="https://download.pytorch.org/whl/cu126"
else
  TORCH_VERSION="2.13.0+cpu"
  TORCH_INDEX="https://download.pytorch.org/whl/cpu"
fi
step "Installing PyTorch ${RESOLVED_BACKEND} runtime inside .venv"
"${PYTHON}" -m pip install --upgrade --force-reinstall --no-cache-dir \
  --progress-bar off --disable-pip-version-check "torch==${TORCH_VERSION}" \
  --index-url "${TORCH_INDEX}"

"${PYTHON}" -m pip install -e ".[capture,transcription,diarization,nvidia-asr,pyannote-diarization]"
"${PYTHON}" -m pip install "huggingface-hub>=0.27,<2"

LLAMA_BACKEND="cpu"
if [[ "$(uname -s)" == "Darwin" ]]; then
  LLAMA_BACKEND="metal"
elif [[ "${RESOLVED_BACKEND}" == "cuda" && "${PYTHON_MINOR}" -le 12 ]]; then
  LLAMA_BACKEND="cuda"
elif [[ "${RESOLVED_BACKEND}" == "cuda" ]]; then
  echo "CUDA PyTorch is installed for transcription models, but the prebuilt llama.cpp CUDA wheel requires Python 3.10-3.12. Local summaries will use CPU."
fi
LLAMA_INDEX="https://abetlen.github.io/llama-cpp-python/whl/${LLAMA_BACKEND}"
echo "PyTorch backend: ${RESOLVED_BACKEND}"
echo "llama.cpp backend: ${LLAMA_BACKEND}"
if ! "${PYTHON}" -m pip install "llama-cpp-python>=0.3.8,<1" \
  --extra-index-url "${LLAMA_INDEX}"; then
  if [[ "${AI_BACKEND}" == "auto" && "${LLAMA_BACKEND}" == "cuda" ]]; then
    echo "Accelerated llama.cpp wheel unavailable; using the portable CPU wheel."
    "${PYTHON}" -m pip install --force-reinstall "llama-cpp-python>=0.3.8,<1" \
      --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/cpu"
  else
    echo "llama.cpp could not be installed for backend '${LLAMA_BACKEND}'." >&2
    exit 1
  fi
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
  MODEL_ARGUMENTS=(
    -m local_meeting_ai.model_setup
    --models all
    --whisper-model "${WHISPER_MODEL}"
  )
  if [[ -n "${MODELS_DIRECTORY}" ]]; then
    MODEL_ARGUMENTS+=(--models-dir "${MODELS_DIRECTORY}")
  fi
  "${PYTHON}" "${MODEL_ARGUMENTS[@]}"
fi

step "Verifying the installation"
"${PYTHON}" -m pip check
"${PYTHON}" scripts/check_environment.py

printf "\n\033[32mMeet2Notes is ready.\033[0m\n"
echo "Run: .venv/bin/meet2notes"

if [[ "${START}" == "true" ]]; then
  "${ENVIRONMENT_ROOT}/bin/meet2notes"
fi
