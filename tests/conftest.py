from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_meeting_ai.api.app import create_app
from local_meeting_ai.config import AppSettings


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "localmeet-data"


@pytest.fixture
def settings(data_dir: Path) -> AppSettings:
    return AppSettings(
        data_dir=data_dir,
        testing=True,
        open_browser=False,
        max_upload_mb=2,
        log_level="WARNING",
    )


@pytest.fixture
def client(settings: AppSettings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
