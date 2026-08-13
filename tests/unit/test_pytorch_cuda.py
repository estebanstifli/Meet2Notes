from __future__ import annotations

from typing import Any

from local_meeting_ai.infrastructure import pytorch_cuda
from local_meeting_ai.infrastructure.pytorch_cuda import PytorchCudaRuntime


def test_cuda_install_uses_the_private_interpreter_and_cuda_index(
    monkeypatch: Any,
) -> None:
    commands: list[list[str]] = []

    class CompletedProcess:
        stdout = iter(["Collecting torch", "Successfully installed torch"])

        def wait(self) -> int:
            return 0

    def fake_popen(command: list[str], **_kwargs: Any) -> CompletedProcess:
        commands.append(command)
        return CompletedProcess()

    monkeypatch.setattr(
        pytorch_cuda,
        "_torch_status",
        lambda: ("2.13.0+cpu", None, False),
    )
    monkeypatch.setattr(pytorch_cuda, "_installed_torch_version", lambda: "2.13.0+cpu")
    monkeypatch.setattr(pytorch_cuda.shutil, "which", lambda _name: "nvidia-smi")
    monkeypatch.setattr(pytorch_cuda.subprocess, "Popen", fake_popen)

    result = PytorchCudaRuntime("C:/Meet2Notes/.venv/Scripts/python.exe")._install_sync()

    assert result["restart_required"] is True
    assert commands == [
        [
            "C:/Meet2Notes/.venv/Scripts/python.exe",
            "-m",
            "pip",
            "install",
            "--upgrade",
            "--force-reinstall",
            "--no-cache-dir",
            "--progress-bar",
            "off",
            "--disable-pip-version-check",
            "torch==2.13.0+cu126",
            "--index-url",
            "https://download.pytorch.org/whl/cu126",
        ]
    ]


def test_cuda_status_requires_restart_without_reinstalling_the_cuda_wheel(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        pytorch_cuda,
        "_torch_status",
        lambda: ("2.13.0+cpu", None, False),
    )
    monkeypatch.setattr(
        pytorch_cuda,
        "_installed_torch_version",
        lambda: "2.13.0+cu126",
    )
    monkeypatch.setattr(pytorch_cuda.shutil, "which", lambda _name: "nvidia-smi")

    status = PytorchCudaRuntime("C:/Meet2Notes/.venv/Scripts/python.exe").status()

    assert status["state"] == "restart_required"
    assert status["restart_required"] is True
    assert status["cuda_wheel_installed"] is True
    assert status["can_install"] is False
