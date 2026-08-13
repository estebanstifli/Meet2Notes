from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import logging
import os
import shutil
import subprocess
import sys
import threading
from typing import Any

from local_meeting_ai.domain.errors import CapabilityUnavailableError, ValidationError

logger = logging.getLogger(__name__)

PYTORCH_CUDA_VERSION = "2.13.0+cu126"
PYTORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu126"


class PytorchCudaRuntime:
    """Install the CUDA PyTorch wheel into this application's virtual environment."""

    def __init__(self, python_executable: str | None = None) -> None:
        self.python_executable = python_executable or sys.executable
        self._install_lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        torch_version, cuda_build, cuda_available = _torch_status()
        installed_torch_version = _installed_torch_version()
        cuda_wheel_installed = _is_cuda_wheel(installed_torch_version)
        # Pip can replace the wheel while this server still has the old CPU
        # torch module imported. Detect that split explicitly: another pip
        # install cannot fix it, but restarting the process does.
        restart_required = cuda_wheel_installed and not _is_cuda_wheel(torch_version)
        nvidia_gpu_detected = shutil.which("nvidia-smi") is not None
        is_virtual_environment = sys.prefix != sys.base_prefix
        if cuda_available:
            state = "ready"
        elif restart_required:
            state = "restart_required"
        elif cuda_wheel_installed:
            state = "cuda_unavailable"
        elif torch_version:
            state = "cpu_only"
        else:
            state = "not_installed"
        message = None
        if restart_required:
            message = (
                "CUDA PyTorch is already installed in .venv. Restart Meet2Notes "
                "to activate it; do not install it again."
            )
        elif state == "cuda_unavailable":
            message = (
                "A CUDA PyTorch wheel is installed, but CUDA is unavailable to the "
                "current process. Check the NVIDIA driver before reinstalling PyTorch."
            )
        return {
            "state": state,
            "torch_version": torch_version,
            "installed_torch_version": installed_torch_version,
            "torch_cuda_build": cuda_build,
            "cuda_available": cuda_available,
            "cuda_wheel_installed": cuda_wheel_installed,
            "nvidia_gpu_detected": nvidia_gpu_detected,
            "is_virtual_environment": is_virtual_environment,
            "can_install": (
                nvidia_gpu_detected
                and is_virtual_environment
                and not cuda_wheel_installed
            ),
            "target_version": PYTORCH_CUDA_VERSION,
            "restart_required": restart_required,
            "message": message,
        }

    async def install(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._install_sync)

    def _install_sync(self) -> dict[str, Any]:
        status = self.status()
        if status["cuda_available"]:
            logger.info("CUDA-enabled PyTorch is already active in the Meet2Notes environment")
            return {
                **status,
                "restart_required": False,
                "message": "CUDA-enabled PyTorch is already active.",
            }
        if status["restart_required"]:
            logger.info("CUDA PyTorch is installed; a Meet2Notes restart is required")
            return status
        if status["cuda_wheel_installed"]:
            raise CapabilityUnavailableError(
                "A CUDA PyTorch wheel is already installed, but CUDA is unavailable. "
                "Check the NVIDIA driver instead of reinstalling PyTorch."
            )
        if not status["is_virtual_environment"]:
            raise CapabilityUnavailableError(
                "CUDA PyTorch can only be installed from Meet2Notes' private .venv. "
                "Start the application with start.bat and try again."
            )
        if not status["nvidia_gpu_detected"]:
            raise CapabilityUnavailableError(
                "No NVIDIA GPU driver was detected. CUDA PyTorch was not installed."
            )
        if not self._install_lock.acquire(blocking=False):
            raise ValidationError("A CUDA PyTorch installation is already running.")
        try:
            command = [
                self.python_executable,
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--force-reinstall",
                "--no-cache-dir",
                "--progress-bar",
                "off",
                "--disable-pip-version-check",
                f"torch=={PYTORCH_CUDA_VERSION}",
                "--index-url",
                PYTORCH_CUDA_INDEX,
            ]
            logger.info(
                "Installing CUDA-enabled PyTorch %s into the private .venv",
                PYTORCH_CUDA_VERSION,
            )
            environment = dict(os.environ)
            environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
            environment["PIP_PROGRESS_BAR"] = "off"
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=environment,
                creationflags=creationflags,
            )
            assert process.stdout is not None
            for line in process.stdout:
                message = line.strip()
                if message:
                    logger.info("PyTorch CUDA installer | %s", message)
            return_code = process.wait()
            if return_code != 0:
                logger.error("CUDA PyTorch installation failed with exit code %d", return_code)
                raise CapabilityUnavailableError(
                    "CUDA PyTorch could not be installed. Review the installation log."
                )
            logger.info(
                "CUDA PyTorch packages were installed in .venv; restart Meet2Notes "
                "before loading GPU models"
            )
            return {
                **status,
                "state": "restart_required",
                "restart_required": True,
                "message": (
                    "CUDA PyTorch was installed in .venv. Restart Meet2Notes "
                    "to activate GPU acceleration."
                ),
            }
        finally:
            self._install_lock.release()


def _torch_status() -> tuple[str | None, str | None, bool]:
    try:
        torch = importlib.import_module("torch")
        version = str(getattr(torch, "__version__", "")) or None
        cuda_build = getattr(getattr(torch, "version", None), "cuda", None)
        cuda_available = bool(torch.cuda.is_available())
        return version, str(cuda_build) if cuda_build else None, cuda_available
    except (ImportError, OSError, RuntimeError):
        return _installed_torch_version(), None, False


def _installed_torch_version() -> str | None:
    try:
        return importlib.metadata.version("torch")
    except importlib.metadata.PackageNotFoundError:
        return None


def _is_cuda_wheel(version: str | None) -> bool:
    return bool(version and "+cu" in version.lower())
