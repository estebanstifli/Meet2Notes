#!/usr/bin/env bash
set -euo pipefail

INSTALLER_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENVIRONMENT_ROOT="${INSTALLER_ROOT}/.venv"
MODELS="all"
WHISPER_MODEL="small"
AI_BACKEND="auto"
REINSTALL_AI_RUNTIME="false"
DEV="false"
START="false"

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

Options:
  --ai-backend auto|cpu|cuda|metal
                                 Select the llama.cpp backend (default: auto)
  --reinstall-ai-runtime         Replace an existing llama.cpp installation
  --no-models                    Do not download AI models during setup
  --whisper-model MODEL          Select tiny/base/small/medium/large-v3/etc.
  --dev                          Install development tools
  --start                        Launch Meet2Notes after installation
  -h, --help                     Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --no-models) MODELS="none" ;;
    --ai-backend)
      shift
      AI_BACKEND="${1:?Missing AI backend (auto, cpu, cuda, or metal)}"
      case "${AI_BACKEND}" in
        auto|cpu|cuda|metal) ;;
        *)
          echo "Unsupported AI backend: ${AI_BACKEND}" >&2
          exit 2
          ;;
      esac
      ;;
    --reinstall-ai-runtime) REINSTALL_AI_RUNTIME="true" ;;
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
RESOLVED_AI_BACKEND="${AI_BACKEND}"
if [[ "${RESOLVED_AI_BACKEND}" == "auto" && "$(uname -s)" == "Darwin" ]]; then
  RESOLVED_AI_BACKEND="metal"
elif [[ "${RESOLVED_AI_BACKEND}" == "auto" ]] \
  && command -v nvidia-smi >/dev/null 2>&1 \
  && [[ "${PYTHON_MINOR}" -le 12 ]]; then
  RESOLVED_AI_BACKEND="cuda"
elif [[ "${RESOLVED_AI_BACKEND}" == "auto" ]]; then
  RESOLVED_AI_BACKEND="cpu"
fi

if [[ "${RESOLVED_AI_BACKEND}" == "cuda" && "${PYTHON_MINOR}" -gt 12 ]]; then
  echo "The prebuilt llama.cpp CUDA wheel requires Python 3.10-3.12." >&2
  exit 1
fi
if [[ "${RESOLVED_AI_BACKEND}" == "metal" && "$(uname -s)" != "Darwin" ]]; then
  echo "The Metal backend is available only on macOS." >&2
  exit 1
fi

if [[ "${RESOLVED_AI_BACKEND}" == "metal" ]]; then
  LLAMA_INDEX="https://abetlen.github.io/llama-cpp-python/whl/metal"
elif [[ "${RESOLVED_AI_BACKEND}" == "cuda" ]]; then
  LLAMA_INDEX="https://abetlen.github.io/llama-cpp-python/whl/cu124"
fi
echo "llama.cpp backend: ${RESOLVED_AI_BACKEND}"
LLAMA_ARGUMENTS=(
  -m pip install "llama-cpp-python>=0.3.8,<1"
  --extra-index-url "${LLAMA_INDEX}"
)
if [[ "${REINSTALL_AI_RUNTIME}" == "true" ]]; then
  LLAMA_ARGUMENTS+=(--force-reinstall --no-cache-dir)
fi
if ! "${PYTHON}" "${LLAMA_ARGUMENTS[@]}"; then
  if [[ "${AI_BACKEND}" == "auto" && "${RESOLVED_AI_BACKEND}" != "cpu" ]]; then
    echo "Accelerated llama.cpp wheel unavailable; using the portable CPU wheel."
    "${PYTHON}" -m pip install --force-reinstall "llama-cpp-python>=0.3.8,<1" \
      --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/cpu"
  else
    echo "llama-cpp-python could not be installed for '${RESOLVED_AI_BACKEND}'." >&2
    exit 1
  fi
fi

if [[ "${DEV}" == "true" ]]; then
  step "Installing development tools"
  "${PYTHON}" -m pip install -e ".[dev]"
fi

if ! command -v ffmpeg >/dev/null 2>&1 \
  || ! command -v ffprobe >/dev/null 2>&1 \
  || ! ffmpeg -version >/dev/null 2>&1 \
  || ! ffprobe -version >/dev/null 2>&1; then
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
echo "Re-running install.sh safely reuses the environment and downloaded models."

if [[ "${START}" == "true" ]]; then
  "${ENVIRONMENT_ROOT}/bin/meet2notes"
fi
