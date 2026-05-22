"""Load and merge MCP server configuration for langchain-mcp-adapters and OpenHands."""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

from langchain_mcp_adapters.sessions import Connection

_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")
_BASIC_AUTH_RE = re.compile(r"\$\{basic:([^}:]+)(?::([^}]+))?\}")
_CURSOR_MCP_PATH = Path.home() / ".cursor" / "mcp.json"


def load_mcp_config_path() -> Path:
    raw = os.getenv("MCP_CONFIG_PATH", "")
    if raw:
        return Path(raw).expanduser()
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "mcp.config.json"


def _basic_auth_b64(email_var: str, token_var: str | None) -> str:
    email = os.environ.get(email_var, "")
    if token_var:
        token = os.environ.get(token_var, "")
    else:
        token = os.environ.get("JIRA_API_TOKEN") or os.environ.get("JIRA_TOKEN", "")
    if not email or not token:
        return f"${{basic:{email_var}" + (f":{token_var}" if token_var else "") + "}"
    return base64.b64encode(f"{email}:{token}".encode()).decode()


def _substitute_env(value: str) -> str:
    value = _BASIC_AUTH_RE.sub(
        lambda m: _basic_auth_b64(m.group(1), m.group(2)),
        value,
    )
    return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)


def _substitute_env_deep(obj: Any) -> Any:
    if isinstance(obj, str):
        return _substitute_env(obj)
    if isinstance(obj, dict):
        return {k: _substitute_env_deep(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_env_deep(v) for v in obj]
    return obj


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return {}
    return data


def _normalize_server_name(name: str) -> str:
    return name.strip().lower().replace(" ", "_")


def _merge_server_maps(
    base: dict[str, Any],
    override: dict[str, Any],
) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for raw_name, entry in override.get("mcpServers", {}).items():
        if not isinstance(entry, dict):
            continue
        key = _normalize_server_name(raw_name)
        if key in merged and isinstance(merged[key], dict):
            combined = dict(merged[key])
            combined.update(entry)
            merged[key] = combined
        else:
            merged[key] = dict(entry)
    return {"mcpServers": merged}


def load_raw_mcp_config() -> dict[str, Any]:
    """Load project config, optionally merged with ~/.cursor/mcp.json."""
    project_path = load_mcp_config_path()
    project = _read_json(project_path)

    if os.getenv("MCP_USE_CURSOR_CONFIG", "").lower() in ("1", "true", "yes"):
        cursor = _read_json(_CURSOR_MCP_PATH)
        if cursor:
            cursor_servers: dict[str, Any] = {}
            for raw_name, entry in cursor.get("mcpServers", {}).items():
                if isinstance(entry, dict):
                    cursor_servers[_normalize_server_name(raw_name)] = entry
            project = _merge_server_maps(
                {"mcpServers": cursor_servers},
                project,
            )

    return _substitute_env_deep(project)


def _is_enabled(entry: dict[str, Any]) -> bool:
    enabled = entry.get("enabled")
    if enabled is None:
        return True
    if isinstance(enabled, bool):
        return enabled
    return str(enabled).lower() not in ("0", "false", "no")


def server_entry_to_connection(entry: dict[str, Any]) -> Connection:
    """Convert a config entry to a langchain-mcp-adapters Connection."""
    transport = entry.get("transport")
    if transport:
        conn: dict[str, Any] = {"transport": transport}
        for key in ("command", "args", "env", "cwd", "url", "headers"):
            if key in entry:
                conn[key] = entry[key]
        return conn  # type: ignore[return-value]

    if "url" in entry:
        return {
            "transport": "http",
            "url": str(entry["url"]),
            **({"headers": entry["headers"]} if "headers" in entry else {}),
        }

    if "command" in entry:
        return {
            "transport": "stdio",
            "command": str(entry["command"]),
            "args": list(entry.get("args", [])),
            **({"env": entry["env"]} if "env" in entry else {}),
        }

    msg = "MCP server entry must specify transport, url, or command"
    raise ValueError(msg)


def load_connections(*, enabled_only: bool = True) -> dict[str, Connection]:
    """Build langchain MultiServerMCPClient connection map."""
    raw = load_raw_mcp_config()
    connections: dict[str, Connection] = {}
    for raw_name, entry in raw.get("mcpServers", {}).items():
        if not isinstance(entry, dict):
            continue
        if enabled_only and not _is_enabled(entry):
            continue
        name = _normalize_server_name(raw_name)
        try:
            connections[name] = server_entry_to_connection(entry)
        except ValueError:
            continue
    return connections


def build_openhands_mcp_config(*, http_only: bool = False) -> dict[str, Any] | None:
    """Build OpenHands Agent mcp_config dict from project MCP settings."""
    if os.getenv("OPENHANDS_MCP_ENABLED", "").lower() not in ("1", "true", "yes"):
        return None

    raw = load_raw_mcp_config()
    servers: dict[str, Any] = {}
    for raw_name, entry in raw.get("mcpServers", {}).items():
        if not isinstance(entry, dict) or not _is_enabled(entry):
            continue
        if http_only and "url" not in entry:
            continue
        if not http_only and "url" in entry and "command" not in entry:
            pass

        name = _normalize_server_name(raw_name)
        oh_entry: dict[str, Any] = {}
        if "url" in entry:
            oh_entry["url"] = entry["url"]
            if entry.get("auth"):
                oh_entry["auth"] = entry["auth"]
        elif "command" in entry:
            oh_entry["command"] = entry["command"]
            oh_entry["args"] = entry.get("args", [])
            if entry.get("env"):
                oh_entry["env"] = {
                    k: _substitute_env(v) if isinstance(v, str) else v
                    for k, v in entry["env"].items()
                }
        else:
            continue
        servers[name] = oh_entry

    if not servers:
        return None
    return {"mcpServers": servers}


def get_server_names(*, http_only: bool = False) -> list[str]:
    raw = load_raw_mcp_config()
    names: list[str] = []
    for raw_name, entry in raw.get("mcpServers", {}).items():
        if not isinstance(entry, dict) or not _is_enabled(entry):
            continue
        if http_only and "url" not in entry:
            continue
        names.append(_normalize_server_name(raw_name))
    return names
