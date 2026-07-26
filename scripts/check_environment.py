from __future__ import annotations

import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def command_report(name: str) -> dict[str, str | None]:
    executable = shutil.which(name)
    version: str | None = None
    if executable:
        try:
            completed = subprocess.run(
                [executable, "-version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            output = completed.stdout or completed.stderr
            version = output.splitlines()[0] if output else None
        except (OSError, subprocess.SubprocessError):
            version = None
    return {"path": executable, "version": version}


def package_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def main() -> None:
    report: dict[str, Any] = {
        "python": sys.version.split()[0],
        "python_supported": sys.version_info >= (3, 11),
        "platform": platform.platform(),
        "executables": {
            "ffmpeg": command_report("ffmpeg"),
            "ffprobe": command_report("ffprobe"),
        },
        "python_packages": {
            "faster_whisper": package_available("faster_whisper"),
            "sherpa_onnx": package_available("sherpa_onnx"),
            "llama_cpp": package_available("llama_cpp"),
            "pyaudiowpatch": package_available("pyaudiowpatch"),
            "sounddevice": package_available("sounddevice"),
        },
    }
    try:
        from local_meeting_ai.config import AppSettings
        from local_meeting_ai.paths import AppPaths

        paths = AppPaths.from_settings(AppSettings())
        report["data_directory"] = str(paths.root)
        report["models_directory"] = str(paths.models)
        existing_path = next(
            (path for path in (paths.root, paths.root.parent) if path.exists()),
            Path.cwd(),
        )
        report["free_space_gib"] = round(
            shutil.disk_usage(existing_path).free / (1024**3),
            2,
        )
    except (ImportError, OSError, ValueError):
        report["data_directory"] = None
        report["models_directory"] = None
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
