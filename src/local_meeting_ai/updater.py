from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from local_meeting_ai import __version__
from local_meeting_ai.config import AppSettings
from local_meeting_ai.infrastructure.database.connection import Database
from local_meeting_ai.infrastructure.database.migrations import MigrationRunner
from local_meeting_ai.instance_lock import AlreadyRunningError, InstanceLock
from local_meeting_ai.paths import AppPaths, installation_directory

REPOSITORY = "estebanstifli/Meet2Notes"
RELEASES_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
CACHE_TTL = timedelta(hours=24)
RELEASE_TAG = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
CACHE_FILE = ".meet2notes-update-cache.json"
REQUEST_FILE = ".meet2notes-update-request.json"


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    tag: str
    version: str
    name: str
    url: str
    published_at: str | None = None


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = RELEASE_TAG.fullmatch(value.strip())
    if not match:
        raise UpdateError(f"Unsupported release version: {value}")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def is_newer_version(candidate: str, current: str = __version__) -> bool:
    return _version_tuple(candidate) > _version_tuple(current)


def fetch_latest_release(*, timeout: float = 3.0) -> ReleaseInfo | None:
    request = urllib.request.Request(
        os.environ.get("M2N_UPDATE_RELEASES_API", RELEASES_API),
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"Meet2Notes/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise UpdateError(f"GitHub returned HTTP {error.code}") from error
    except (OSError, TimeoutError, json.JSONDecodeError) as error:
        raise UpdateError("The release service is unavailable") from error

    if payload.get("draft") or payload.get("prerelease"):
        return None
    tag = str(payload.get("tag_name") or "").strip()
    version = tag.removeprefix("v")
    _version_tuple(version)
    return ReleaseInfo(
        tag=tag,
        version=version,
        name=str(payload.get("name") or tag),
        url=str(payload.get("html_url") or f"https://github.com/{REPOSITORY}/releases"),
        published_at=(str(payload["published_at"]) if payload.get("published_at") else None),
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)


def latest_release(*, force: bool = False) -> ReleaseInfo | None:
    cache_path = installation_directory() / CACHE_FILE
    cached = _read_json(cache_path)
    if cached and not force:
        try:
            checked_at = datetime.fromisoformat(str(cached["checked_at"]))
            if datetime.now(UTC) - checked_at < CACHE_TTL:
                deferred_until = cached.get("deferred_until")
                if deferred_until and datetime.now(UTC) < datetime.fromisoformat(
                    str(deferred_until)
                ):
                    return None
                release = cached.get("release")
                return ReleaseInfo(**release) if isinstance(release, dict) else None
        except (KeyError, TypeError, ValueError):
            pass

    try:
        release = fetch_latest_release()
    except UpdateError:
        _write_json(
            cache_path,
            {"checked_at": datetime.now(UTC).isoformat(), "release": None},
        )
        raise
    _write_json(
        cache_path,
        {
            "checked_at": datetime.now(UTC).isoformat(),
            "release": asdict(release) if release else None,
        },
    )
    return release


def defer_release(release: ReleaseInfo) -> None:
    _write_json(
        installation_directory() / CACHE_FILE,
        {
            "checked_at": datetime.now(UTC).isoformat(),
            "deferred_until": (datetime.now(UTC) + CACHE_TTL).isoformat(),
            "release": asdict(release),
        },
    )


def _clean_app_args(arguments: list[str]) -> list[str]:
    return arguments[1:] if arguments and arguments[0] == "--" else arguments


def _explicit_data_directory(arguments: list[str]) -> Path | None:
    for index, argument in enumerate(arguments):
        if argument == "--data-dir" and index + 1 < len(arguments):
            return Path(arguments[index + 1]).expanduser().resolve()
        if argument.startswith("--data-dir="):
            return Path(argument.split("=", maxsplit=1)[1]).expanduser().resolve()
    return None


def resolved_data_directory(app_args: list[str]) -> Path:
    explicit = _explicit_data_directory(app_args)
    settings = AppSettings(data_dir=explicit) if explicit else AppSettings()
    return AppPaths.from_settings(settings).root


def prepare_update(release: ReleaseInfo, app_args: list[str]) -> Path:
    request_path = installation_directory() / REQUEST_FILE
    _write_json(
        request_path,
        {
            "schema": 1,
            "repository": REPOSITORY,
            "current_version": __version__,
            "target_version": release.version,
            "tag": release.tag,
            "release_url": release.url,
            "data_directory": str(resolved_data_directory(app_args)),
            "app_args": app_args,
            "created_at": datetime.now(UTC).isoformat(),
        },
    )
    return request_path


def _load_update_request(path: Path) -> dict[str, Any]:
    request = _read_json(path)
    if not request or request.get("schema") != 1:
        raise UpdateError("The update request is missing or invalid")
    if request.get("repository") != REPOSITORY:
        raise UpdateError("The update request targets another repository")
    _version_tuple(str(request.get("target_version") or ""))
    tag = str(request.get("tag") or "")
    if tag not in {str(request["target_version"]), f"v{request['target_version']}"}:
        raise UpdateError("The release tag does not match its version")
    return request


def backup_database(request_path: Path) -> Path | None:
    request = _load_update_request(request_path)
    data_directory = Path(str(request["data_directory"])).expanduser().resolve()
    database_path = data_directory / "app.db"
    if not database_path.is_file():
        request["database_backup"] = None
        _write_json(request_path, request)
        return None

    lock = InstanceLock(
        data_directory / "meet2notes.instance.lock",
        {"pid": os.getpid(), "purpose": "update-backup"},
    )
    try:
        lock.acquire()
    except AlreadyRunningError as error:
        raise UpdateError("Meet2Notes must be stopped before updating") from error

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_directory = data_directory / "backups"
    backup_directory.mkdir(parents=True, exist_ok=True)
    backup_path = backup_directory / (
        f"pre-update-{request['current_version']}-to-{request['target_version']}-{timestamp}.db"
    )
    try:
        with sqlite3.connect(database_path) as source, sqlite3.connect(backup_path) as target:
            source.backup(target)
        with sqlite3.connect(backup_path) as connection:
            result = connection.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            backup_path.unlink(missing_ok=True)
            raise UpdateError("The database backup did not pass SQLite integrity checks")
    finally:
        lock.release()

    request["database_backup"] = str(backup_path)
    _write_json(request_path, request)
    return backup_path


def validate_migrations(request_path: Path) -> None:
    request = _load_update_request(request_path)
    raw_backup = request.get("database_backup")
    if not raw_backup:
        return
    backup_path = Path(str(raw_backup)).resolve()
    if not backup_path.is_file():
        raise UpdateError("The pre-update database backup is missing")
    validation_path = backup_path.with_name(f"{backup_path.stem}-migration-test.db")
    shutil.copy2(backup_path, validation_path)
    try:
        database = Database(validation_path)
        MigrationRunner(database).apply()
        with database.read() as connection:
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchone()
        if not integrity or integrity[0] != "ok" or foreign_keys is not None:
            raise UpdateError("The upgraded database copy failed integrity checks")
    finally:
        validation_path.unlink(missing_ok=True)
        validation_path.with_suffix(".db-wal").unlink(missing_ok=True)
        validation_path.with_suffix(".db-shm").unlink(missing_ok=True)


def _confirm(release: ReleaseInfo) -> bool:
    print()
    print(f"Meet2Notes {release.version} is available (installed: {__version__}).")
    print(release.url)
    answer = input("Update now? [y/N]: ").strip().lower()
    return answer in {"y", "yes", "s", "si", "sí"}


def _check_command(arguments: argparse.Namespace, *, manual: bool) -> int:
    app_args = _clean_app_args(arguments.app_args)
    try:
        release = latest_release(force=bool(arguments.force))
    except UpdateError as error:
        if manual:
            print(f"Update check failed: {error}", file=sys.stderr)
            return 1
        return 0
    if release is None or not is_newer_version(release.version):
        if manual:
            print(f"Meet2Notes {__version__} is up to date.")
        return 0
    if arguments.interactive and not _confirm(release):
        if not manual:
            defer_release(release)
        return 0
    prepare_update(release, app_args)
    return 10


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Meet2Notes safe release updater")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "prepare"):
        action = commands.add_parser(command)
        action.add_argument("--interactive", action="store_true")
        action.add_argument("--force", action="store_true")
        action.add_argument("app_args", nargs=argparse.REMAINDER)
    for command in ("backup", "validate"):
        action = commands.add_parser(command)
        action.add_argument(
            "--request-file",
            type=Path,
            default=installation_directory() / REQUEST_FILE,
        )
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        if arguments.command == "check":
            return _check_command(arguments, manual=False)
        if arguments.command == "prepare":
            return _check_command(arguments, manual=True)
        if arguments.command == "backup":
            backup = backup_database(arguments.request_file.resolve())
            print(f"Database backup: {backup}" if backup else "No existing database to back up.")
            return 0
        validate_migrations(arguments.request_file.resolve())
        print("Database migrations validated on the backup copy.")
        return 0
    except UpdateError as error:
        print(f"Update failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
