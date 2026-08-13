from __future__ import annotations

from pathlib import Path

import pytest

from local_meeting_ai.instance_lock import (
    AlreadyRunningError,
    InstanceLock,
    instance_metadata,
)


def test_instance_lock_prevents_duplicate_and_recovers_after_release(
    tmp_path: Path,
) -> None:
    path = tmp_path / "meet2notes.instance.lock"
    metadata = instance_metadata(host="127.0.0.1", port=8765)
    first = InstanceLock(path, metadata)
    second = InstanceLock(path, metadata)

    first.acquire()
    try:
        with pytest.raises(AlreadyRunningError) as duplicate:
            second.acquire()
        assert duplicate.value.metadata["pid"] == metadata["pid"]
        assert duplicate.value.metadata["url"] == "http://127.0.0.1:8765"
    finally:
        first.release()

    with InstanceLock(path, metadata):
        assert path.exists()


def test_wildcard_host_metadata_uses_loopback_browser_url() -> None:
    metadata = instance_metadata(host="0.0.0.0", port=9000)
    assert metadata["url"] == "http://127.0.0.1:9000"
