"""Async MCP tool client wrapping langchain-mcp-adapters."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.types import CallToolResult, TextContent

from agent_graph.mcp.config import load_connections


def _format_tool_result(result: CallToolResult) -> str:
    parts: list[str] = []
    for block in result.content:
        if isinstance(block, TextContent):
            parts.append(block.text)
        else:
            parts.append(str(block))
    if result.isError:
        return f"MCP tool error: {' '.join(parts)}"
    return "\n".join(parts) if parts else ""


class McpToolClient:
    """Call tools on configured MCP servers via short-lived sessions."""

    def __init__(self, connections: dict[str, Any] | None = None) -> None:
        self._connections = connections if connections is not None else load_connections()
        self._client = MultiServerMCPClient(self._connections)

    @property
    def server_names(self) -> list[str]:
        return list(self._connections.keys())

    def has_server(self, name: str) -> bool:
        return name in self._connections

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
    ) -> str:
        async with self._client.session(server) as session:
            result = await session.call_tool(tool, arguments or {})
            return _format_tool_result(result)

    def call_tool_sync(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
    ) -> str:
        return asyncio.run(self.call_tool(server, tool, arguments))

    async def list_tools(self, server: str) -> list[str]:
        async with self._client.session(server) as session:
            tools = await session.list_tools()
            return [t.name for t in tools.tools]

    def list_tools_sync(self, server: str) -> list[str]:
        return asyncio.run(self.list_tools(server))


def parse_json_tool_result(text: str) -> Any:
    """Best-effort parse of MCP tool text as JSON."""
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
