from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "install_llama_cpp.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("install_llama_cpp", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakePip:
    def __init__(self, results: list[int]) -> None:
        self.results = results
        self.commands: list[list[str]] = []
        self.constraints: list[str] = []

    def __call__(self, command: list[str], check: bool) -> SimpleNamespace:
        self.commands.append(command)
        constraint_file = Path(command[command.index("--constraint") + 1])
        self.constraints.append(constraint_file.read_text(encoding="utf-8"))
        return SimpleNamespace(returncode=self.results[len(self.commands) - 1])


def test_cpu_backend_has_a_single_attempt() -> None:
    script = load_script()

    assert script.attempts("cpu", allow_cpu_fallback=True) == [(script.REQUIREMENT, "cpu")]


def test_accelerated_backend_steps_down_before_using_cpu_wheels() -> None:
    script = load_script()

    assert script.attempts("metal", allow_cpu_fallback=False) == [
        (script.REQUIREMENT, "metal"),
        (script.KNOWN_GOOD, "metal"),
    ]
    assert script.attempts("metal", allow_cpu_fallback=True) == [
        (script.REQUIREMENT, "metal"),
        (script.KNOWN_GOOD, "metal"),
        (script.REQUIREMENT, "cpu"),
        (script.KNOWN_GOOD, "cpu"),
    ]


def test_install_stops_at_the_first_success_and_keeps_project_constraints() -> None:
    script = load_script()
    pip = FakePip([1, 0])

    assert script.install("metal", allow_cpu_fallback=True, run=pip) is True

    assert len(pip.commands) == 2
    assert script.REQUIREMENT in pip.commands[0]
    assert script.KNOWN_GOOD in pip.commands[1]
    assert pip.commands[1][pip.commands[1].index("--extra-index-url") + 1].endswith("/metal")
    for command, constraints in zip(pip.commands, pip.constraints, strict=True):
        assert "--force-reinstall" not in command
        assert "numpy>=1.26,<2.4" in constraints.splitlines()


def test_install_reports_failure_when_every_attempt_fails() -> None:
    script = load_script()
    pip = FakePip([1, 1, 1, 1])

    assert script.install("metal", allow_cpu_fallback=True, run=pip) is False
    assert len(pip.commands) == 4


def test_project_constraints_drop_extras() -> None:
    script = load_script()

    constraints = script.project_constraints()

    assert not any("[" in constraint.split(";")[0] for constraint in constraints)
    assert any(constraint.startswith("uvicorn>=") for constraint in constraints)
