"""Tests for McpToolClient against a local FastMCP stdio server."""

import sys
from pathlib import Path

import pytest

from agent_graph.mcp.client import McpToolClient
from agent_graph.mcp.context7 import detect_libraries

FIXTURE_SERVER = Path(__file__).resolve().parent / "fixtures" / "test_server.py"


@pytest.mark.asyncio
async def test_mcp_echo_tool():
    connections = {
        "test": {
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(FIXTURE_SERVER)],
        }
    }
    client = McpToolClient(connections)
    result = await client.call_tool("test", "echo", {"message": "hello-mcp"})
    assert "hello-mcp" in result


def test_detect_libraries():
    libs = detect_libraries("Add JWT auth to FastAPI backend with pytest")
    assert "FastAPI" in libs
    assert "pytest" in libs
