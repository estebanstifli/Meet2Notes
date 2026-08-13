from __future__ import annotations

from pathlib import Path

from local_meeting_ai.config import AppSettings
from local_meeting_ai.paths import AppPaths, default_models_directory


def test_default_models_are_outside_platform_user_data(tmp_path: Path) -> None:
    paths = AppPaths.from_settings(AppSettings(data_dir=tmp_path / "user-data"))
    assert paths.models == default_models_directory()
    assert not paths.models.is_relative_to(paths.root)


def test_explicit_models_directory_has_priority(tmp_path: Path) -> None:
    selected = tmp_path / "large-drive" / "models"
    paths = AppPaths.from_settings(
        AppSettings(data_dir=tmp_path / "user-data", models_dir=selected)
    )
    assert paths.models == selected.resolve()
