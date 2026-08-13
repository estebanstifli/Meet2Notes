from __future__ import annotations

from pathlib import Path

import pytest

from local_meeting_ai.config import AppSettings
from local_meeting_ai.domain.errors import ValidationError
from local_meeting_ai.infrastructure.storage import MeetingStorage
from local_meeting_ai.paths import AppPaths


def test_storage_refuses_directory_traversal(tmp_path: Path) -> None:
    paths = AppPaths.from_settings(
        AppSettings(
            data_dir=tmp_path / "data",
            models_dir=tmp_path / "models",
        )
    )
    paths.ensure()
    storage = MeetingStorage(paths, max_upload_bytes=1024)

    with pytest.raises(ValidationError, match="outside meeting storage"):
        storage.delete_meeting("../outside")
