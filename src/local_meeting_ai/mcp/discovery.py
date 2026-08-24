from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from platformdirs import user_data_path

from local_meeting_ai.paths import data_location_file, default_data_directory


class DiscoveryError(RuntimeError):
    """Raised when an MCP backend location is unsafe or invalid."""


def candidate_base_urls() -> list[str]:
    override = os.environ.get("M2N_MCP_BASE_URL", "").strip()
    if override:
        return [_validated_base_url(override)]

    urls: list[str] = []
    for directory in _candidate_data_directories():
        metadata_path = directory / "meet2notes.instance.lock.json"
        try:
            decoded = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(decoded, dict):
            continue
        raw_url = decoded.get("url")
        if not isinstance(raw_url, str) or not raw_url.strip():
            continue
        try:
            url = _validated_base_url(raw_url)
        except DiscoveryError:
            continue
        if url not in urls:
            urls.append(url)

    fallback = _validated_base_url("http://127.0.0.1:8765")
    if fallback not in urls:
        urls.append(fallback)
    return urls


def _candidate_data_directories() -> list[Path]:
    candidates: list[Path] = []
    configured = os.environ.get("M2N_DATA_DIR", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser().resolve())

    marker = data_location_file()
    try:
        marker_state = json.loads(marker.read_text(encoding="utf-8"))
        marker_root = marker_state.get("root") if isinstance(marker_state, dict) else None
        if isinstance(marker_root, str) and marker_root.strip():
            candidates.append(Path(marker_root).expanduser().resolve())
    except (OSError, json.JSONDecodeError):
        pass

    candidates.extend(
        [
            user_data_path("Meet2Notes", appauthor=False).resolve(),
            default_data_directory(),
            user_data_path("LocalMeet2Resume", appauthor=False).resolve(),
        ]
    )
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def _validated_base_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DiscoveryError("Meet2Notes MCP requires a valid HTTP backend URL")
    allow_remote = os.environ.get("M2N_MCP_ALLOW_REMOTE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if not allow_remote and not _is_loopback(parsed.hostname):
        raise DiscoveryError(
            "Meet2Notes MCP only connects to loopback addresses unless "
            "M2N_MCP_ALLOW_REMOTE=1 is explicitly configured"
        )
    return value.strip().rstrip("/")


def _is_loopback(hostname: str) -> bool:
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False
