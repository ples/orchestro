"""Tests for Context7 enrichment helpers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_graph.mcp.context7 import enrich_with_context7


@pytest.mark.asyncio
async def test_enrich_with_context7_mocked():
    mock_client = MagicMock()
    mock_client.has_server.return_value = True
    mock_client.call_tool = AsyncMock(
        side_effect=[
            "Library ID: /tiangolo/fastapi",
            "Use APIRouter and Depends for JWT.",
        ]
    )

    context, tools = await enrich_with_context7(
        "Add JWT authentication to FastAPI",
        client=mock_client,
    )
    assert "FastAPI" in context
    assert "context7/resolve-library-id" in tools
    assert "context7/query-docs" in tools
