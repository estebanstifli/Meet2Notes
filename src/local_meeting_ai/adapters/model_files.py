"""Small, defensive helpers for removing locally managed model files."""

from __future__ import annotations

import shutil
from pathlib import Path

from local_meeting_ai.domain.errors import CapabilityUnavailableError


def remove_managed_model_tree(*, root: Path, target: Path, label: str) -> bool:
    """Remove one known model directory without ever escaping its model root.

    Model locations are application-owned, but resolving and validating the
    relationship here keeps a future configuration change from turning an
    uninstall request into a broad filesystem deletion.
    """

    resolved_root = root.resolve()
    resolved_target = target.resolve()
    try:
        relative = resolved_target.relative_to(resolved_root)
    except ValueError as error:
        raise CapabilityUnavailableError(
            f"Refusing to remove {label}: its files are outside the configured model directory"
        ) from error
    if relative == Path("."):
        raise CapabilityUnavailableError(
            f"Refusing to remove {label}: the configured model root cannot be removed"
        )
    if not target.exists():
        return False
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return True
