"""Local desktop-client configuration for the Meet2Notes MCP server."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

MCP_DEFAULTS = {"enabled": True}
MCP_SERVER_NAME = "meet2notes"


@dataclass(frozen=True, slots=True)
class DesktopClientConfiguration:
    client_id: str
    name: str
    format: str
    path: Path
    content: str


def is_mcp_enabled(preferences: Mapping[str, object]) -> bool:
    configured = preferences.get("mcp")
    if not isinstance(configured, Mapping):
        return bool(MCP_DEFAULTS["enabled"])
    return bool(configured.get("enabled", MCP_DEFAULTS["enabled"]))


def desktop_client_configurations(
    *,
    platform_name: str | None = None,
    home: Path | None = None,
    environment: Mapping[str, str] | None = None,
    python_executable: str | None = None,
) -> dict[str, DesktopClientConfiguration]:
    platform_name = platform_name or sys.platform
    home = home or Path.home()
    environment = environment or os.environ
    command = str(Path(python_executable or sys.executable).resolve())
    args = ["-m", "local_meeting_ai.mcp.server"]
    server = {"command": command, "args": args}

    claude_path = _claude_config_path(platform_name, home, environment)
    claude_content = json.dumps(
        {"mcpServers": {MCP_SERVER_NAME: server}},
        indent=2,
        ensure_ascii=False,
    )
    codex_path = home / ".codex" / "config.toml"
    codex_content = (
        f"[mcp_servers.{MCP_SERVER_NAME}]\n"
        f"command = {json.dumps(command)}\n"
        f"args = {json.dumps(args)}\n"
    )
    return {
        "claude-desktop": DesktopClientConfiguration(
            client_id="claude-desktop",
            name="Claude Desktop",
            format="JSON",
            path=claude_path,
            content=claude_content,
        ),
        "codex-chatgpt": DesktopClientConfiguration(
            client_id="codex-chatgpt",
            name="Codex / ChatGPT Desktop",
            format="TOML",
            path=codex_path,
            content=codex_content,
        ),
    }


def open_desktop_client_config(client_id: str) -> DesktopClientConfiguration:
    configuration = desktop_client_configurations().get(client_id)
    if configuration is None:
        raise ValueError(f"Unknown MCP desktop client: {client_id}")
    configuration.path.parent.mkdir(parents=True, exist_ok=True)
    configuration.path.touch(exist_ok=True)
    if sys.platform.startswith("win"):
        startfile = getattr(os, "startfile", None)
        if startfile is None:
            raise OSError("The operating system cannot open local files")
        startfile(str(configuration.path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(configuration.path)], start_new_session=True)
    else:
        subprocess.Popen(["xdg-open", str(configuration.path)], start_new_session=True)
    return configuration


def _claude_config_path(
    platform_name: str,
    home: Path,
    environment: Mapping[str, str],
) -> Path:
    if platform_name.startswith("win"):
        appdata = environment.get("APPDATA")
        root = Path(appdata) if appdata else home / "AppData" / "Roaming"
        return root / "Claude" / "claude_desktop_config.json"
    if platform_name == "darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    config_root = Path(environment.get("XDG_CONFIG_HOME") or home / ".config")
    return config_root / "Claude" / "claude_desktop_config.json"
