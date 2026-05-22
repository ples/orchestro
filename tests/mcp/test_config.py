"""Tests for MCP configuration loading."""

import json
import os
from pathlib import Path

import pytest

from agent_graph.mcp.config import (
    _substitute_env,
    _substitute_env_deep,
    load_connections,
    load_raw_mcp_config,
    server_entry_to_connection,
)


def test_substitute_basic_auth():
    os.environ["TEST_MCP_EMAIL"] = "user@example.com"
    os.environ["TEST_MCP_TOKEN"] = "secret-token"
    try:
        result = _substitute_env("Basic ${basic:TEST_MCP_EMAIL:TEST_MCP_TOKEN}")
        assert result == "Basic dXNlckBleGFtcGxlLmNvbTpzZWNyZXQtdG9rZW4="
    finally:
        del os.environ["TEST_MCP_EMAIL"]
        del os.environ["TEST_MCP_TOKEN"]


def test_substitute_env_deep():
    os.environ["TEST_MCP_TOKEN"] = "secret-value"
    try:
        result = _substitute_env_deep({"key": "${TEST_MCP_TOKEN}"})
        assert result["key"] == "secret-value"
    finally:
        del os.environ["TEST_MCP_TOKEN"]


def test_server_entry_stdio():
    conn = server_entry_to_connection(
        {
            "transport": "stdio",
            "command": "python",
            "args": ["server.py"],
        }
    )
    assert conn["transport"] == "stdio"
    assert conn["command"] == "python"


def test_server_entry_http():
    conn = server_entry_to_connection({"url": "https://example.com/mcp"})
    assert conn["transport"] == "http"
    assert conn["url"] == "https://example.com/mcp"


def test_load_connections_respects_enabled(tmp_path, monkeypatch):
    config = {
        "mcpServers": {
            "on": {
                "enabled": True,
                "transport": "stdio",
                "command": "echo",
                "args": ["ok"],
            },
            "off": {
                "enabled": False,
                "transport": "stdio",
                "command": "echo",
                "args": ["no"],
            },
        }
    }
    path = tmp_path / "mcp.config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("MCP_CONFIG_PATH", str(path))
    monkeypatch.delenv("MCP_USE_CURSOR_CONFIG", raising=False)

    connections = load_connections()
    assert "on" in connections
    assert "off" not in connections


def test_project_config_exists():
    raw = load_raw_mcp_config()
    assert "mcpServers" in raw
    assert "context7" in raw["mcpServers"]
