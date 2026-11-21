"""Process-wide llama-cpp-python import, attempted at most once.

Python does not keep a module whose import failed, so every later import runs
the package loader again. On Windows each failed attempt of that loader
prepends its library folder to PATH and registers more DLL directories; a
status check that retried a broken runtime eventually made PATH too long for
other native packages to load. Importing here once, and remembering the first
failure, keeps a broken runtime from being retried until the application
restarts.
"""
from __future__ import annotations

import importlib
import importlib.util
import threading
from types import ModuleType

_lock = threading.Lock()
_module: ModuleType | None = None
_error: str | None = None


def load_llama_cpp() -> ModuleType:
    """Return the llama_cpp module, or raise ImportError with the first load failure."""
    global _module, _error
    with _lock:
        if _module is not None:
            return _module
        if _error is None:
            # A missing package is not remembered: looking it up has no side effects.
            if importlib.util.find_spec("llama_cpp") is None:
                raise ImportError("llama-cpp-python is not installed")
            try:
                _module = importlib.import_module("llama_cpp")
                return _module
            except (ImportError, OSError, RuntimeError, AttributeError) as error:
                _error = f"{type(error).__name__}: {error}"
        raise ImportError(
            f"llama-cpp-python could not be loaded ({_error}). "
            "Repair the installation and restart Meet2Notes."
        )


def llama_cpp_error() -> str | None:
    """Return the remembered load failure, if an import has already failed."""
    with _lock:
        return _error


def llama_cpp_usable() -> bool:
    """Report whether the package is installed and has not failed to load.

    Does not import the package, so it is safe for frequent status checks.
    """
    return importlib.util.find_spec("llama_cpp") is not None and llama_cpp_error() is None


def reset_llama_cpp() -> None:
    """Forget the cached module or failure. Intended for tests."""
    global _module, _error
    with _lock:
        _module = None
        _error = None
