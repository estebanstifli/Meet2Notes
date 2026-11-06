#!/usr/bin/env bash
set -euo pipefail

INSTALLER_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENVIRONMENT_ROOT="${INSTALLER_ROOT}/.venv"
MODELS="all"
WHISPER_MODEL="auto"
MODELS_DIRECTORY=""
DEV="false"
START="false"
AI_BACKEND="auto"
LLM_PROFILE="auto"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-models) MODELS="none" ;;
    --llm-profile)
      shift
      LLM_PROFILE="${1:?Missing LLM profile}"
      case "${LLM_PROFILE}" in
        auto|light|bonsai-8b|bonsai-1bit|bonsai-ternary|none) ;;
        *) echo "Invalid --llm-profile" >&2; exit 2 ;;
      esac
      ;;
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
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
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
elif [[ "$(uname -s)" == "Darwin" ]]; then
  # macOS uses the standard CPU/MPS wheels published on PyPI. The
  # download.pytorch.org "+cpu" local-version builds are Windows/Linux only, so
  # "torch==2.13.0+cpu" from the cpu index never resolves on macOS.
  TORCH_VERSION="2.13.0"
  TORCH_INDEX=""
else
  TORCH_VERSION="2.13.0+cpu"
  TORCH_INDEX="https://download.pytorch.org/whl/cpu"
fi
step "Installing PyTorch ${RESOLVED_BACKEND} runtime inside .venv"
TORCH_INSTALL_ARGS=(
  --upgrade --force-reinstall --no-cache-dir
  --progress-bar off --disable-pip-version-check
  "torch==${TORCH_VERSION}"
)
if [[ -n "${TORCH_INDEX}" ]]; then
  TORCH_INSTALL_ARGS+=(--index-url "${TORCH_INDEX}")
fi
"${PYTHON}" -m pip install "${TORCH_INSTALL_ARGS[@]}"

"${PYTHON}" -m pip install -e ".[capture,transcription,diarization,nvidia-asr,pyannote-diarization]"
"${PYTHON}" -m pip install "huggingface-hub>=0.27,<2"
if [[ "${RESOLVED_BACKEND}" == "cuda" ]]; then
  "${PYTHON}" -m local_meeting_ai.infrastructure.linux_cuda --install-if-needed
fi

LLAMA_BACKEND="cpu"
if [[ "$(uname -s)" == "Darwin" ]]; then
  LLAMA_BACKEND="metal"
elif [[ "${RESOLVED_BACKEND}" == "cuda" && "${PYTHON_MINOR}" -le 12 ]]; then
  LLAMA_BACKEND="cuda"
elif [[ "${RESOLVED_BACKEND}" == "cuda" ]]; then
  echo "The Python llama.cpp CUDA wheel requires Python 3.10-3.12; lightweight GGUF models will use CPU. Managed Bonsai uses its own CUDA runtime."
fi
LLAMA_INDEX="https://abetlen.github.io/llama-cpp-python/whl/${LLAMA_BACKEND}"
echo "PyTorch backend: ${RESOLVED_BACKEND}"
echo "llama.cpp backend: ${LLAMA_BACKEND}"
# Prefer the newest compatible release, but some published wheels are corrupt
# (zip members fail their CRC-32 check), so step down to the newest release
# known to have intact wheels before falling back to the portable CPU wheel.
LLAMA_SPECS=("llama-cpp-python>=0.3.8,<1")
if [[ "${LLAMA_BACKEND}" != "cpu" ]]; then
  LLAMA_SPECS+=("llama-cpp-python==0.3.30")
fi
LLAMA_INSTALLED="false"
for LLAMA_SPEC in "${LLAMA_SPECS[@]}"; do
  if "${PYTHON}" -m pip install --force-reinstall --no-cache-dir "${LLAMA_SPEC}" \
    --extra-index-url "${LLAMA_INDEX}"; then
    LLAMA_INSTALLED="true"
    break
  fi
  echo "Could not install ${LLAMA_SPEC} from the ${LLAMA_BACKEND} wheel index (the published wheel may be corrupt); trying the next option."
done
if [[ "${LLAMA_INSTALLED}" != "true" && "${AI_BACKEND}" == "auto" ]]; then
  echo "Accelerated llama.cpp wheel unavailable; using the portable CPU wheel."
  for LLAMA_SPEC in "llama-cpp-python>=0.3.8,<1" "llama-cpp-python==0.3.30"; do
    if "${PYTHON}" -m pip install --force-reinstall --no-cache-dir "${LLAMA_SPEC}" \
      --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/cpu"; then
      LLAMA_INSTALLED="true"
      break
    fi
  done
fi
if [[ "${LLAMA_INSTALLED}" != "true" ]]; then
  echo "llama.cpp could not be installed for backend '${LLAMA_BACKEND}'." >&2
  exit 1
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
    --llm-profile "${LLM_PROFILE}"
    --llm-backend "${AI_BACKEND}"
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
