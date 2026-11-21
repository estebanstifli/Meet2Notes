"""Installer bootstrap: install llama-cpp-python despite broken upstream wheels.

Shared by install.sh and the Pinokio scripts. It tries the newest compatible
release first, then the newest release known to ship intact accelerated wheels,
and optionally the portable CPU wheels. Every attempt honours the version
ranges declared in pyproject.toml, so a retry cannot move numpy or another
shared dependency outside the range the application supports.
"""
from __future__ import annotations

import argparse
import platform
import re
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENT = "llama-cpp-python>=0.3.8,<1"
# Newest release whose accelerated wheels are known to be intact. Later macOS
# Metal wheels were published with members that fail their CRC-32 check
# (abetlen/llama-cpp-python#2379). Used only after the newest release fails.
KNOWN_GOOD = "llama-cpp-python==0.3.30"
WHEEL_INDEX = "https://abetlen.github.io/llama-cpp-python/whl/{channel}"

Runner = Callable[..., Any]


def resolve_backend(backend: str) -> str:
    if backend != "auto":
        return backend
    return "metal" if platform.system() == "Darwin" else "cpu"


def attempts(backend: str, allow_cpu_fallback: bool) -> list[tuple[str, str]]:
    """Return the ordered (requirement, wheel channel) installation attempts."""
    plan = [(REQUIREMENT, backend)]
    if backend != "cpu":
        plan.append((KNOWN_GOOD, backend))
        if allow_cpu_fallback:
            plan += [(REQUIREMENT, "cpu"), (KNOWN_GOOD, "cpu")]
    return plan


def project_constraints(pyproject: Path = ROOT / "pyproject.toml") -> list[str]:
    """Return every requirement declared by the project as a pip constraint."""
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    requirements: list[str] = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        requirements += extra
    # Constraint files reject extras such as "uvicorn[standard]>=0.34".
    return sorted({re.sub(r"^([A-Za-z0-9._-]+)\[[^\]]*\]", r"\1", item) for item in requirements})


def install(
    backend: str,
    allow_cpu_fallback: bool,
    run: Runner = subprocess.run,
    constraints: Sequence[str] | None = None,
) -> bool:
    lines = project_constraints() if constraints is None else list(constraints)
    with tempfile.TemporaryDirectory() as directory:
        constraint_file = Path(directory) / "constraints.txt"
        constraint_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        for requirement, channel in attempts(backend, allow_cpu_fallback):
            print(f"Installing {requirement} ({channel} wheels)", flush=True)
            command = [
                sys.executable, "-m", "pip", "install", "--no-cache-dir",
                # Take the newest published wheel instead of compiling a newer sdist.
                "--prefer-binary", requirement,
                "--extra-index-url", WHEEL_INDEX.format(channel=channel),
                "--constraint", str(constraint_file),
            ]
            if run(command, check=False).returncode == 0:
                return True
            print(
                f"Could not install {requirement} from the {channel} wheels "
                "(the published wheel may be missing or corrupt).",
                flush=True,
            )
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", choices=["auto", "cpu", "metal", "cuda"], default="auto")
    parser.add_argument(
        "--allow-cpu-fallback",
        action="store_true",
        help="Use the portable CPU wheels when the accelerated ones cannot be installed.",
    )
    arguments = parser.parse_args()
    backend = resolve_backend(arguments.backend)
    if install(backend, arguments.allow_cpu_fallback):
        return 0
    print(f"llama.cpp could not be installed for backend '{backend}'.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
