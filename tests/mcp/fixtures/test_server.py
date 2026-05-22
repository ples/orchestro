"""Minimal stdio MCP server for integration tests."""

from fastmcp import FastMCP

mcp = FastMCP("Test")


@mcp.tool()
def echo(message: str) -> str:
    """Echo a message back."""
    return message


if __name__ == "__main__":
    mcp.run(transport="stdio")
