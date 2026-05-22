"""Context7 documentation enrichment via MCP."""

from __future__ import annotations

import os
import re

from agent_graph.mcp.client import McpToolClient

CONTEXT7_SERVER = "context7"
MAX_QUERY_DOCS = 3

_LIBRARY_HINTS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bfastapi\b", re.I), "FastAPI"),
    (re.compile(r"\blanggraph\b", re.I), "LangGraph"),
    (re.compile(r"\blangchain\b", re.I), "LangChain"),
    (re.compile(r"\breact\b", re.I), "React"),
    (re.compile(r"\bnext\.?js\b", re.I), "Next.js"),
    (re.compile(r"\bdjango\b", re.I), "Django"),
    (re.compile(r"\bpytest\b", re.I), "pytest"),
    (re.compile(r"\bplaywright\b", re.I), "Playwright"),
    (re.compile(r"\btypescript\b", re.I), "TypeScript"),
    (re.compile(r"\bopenai\b", re.I), "OpenAI"),
    (re.compile(r"\bpydantic\b", re.I), "Pydantic"),
]


def _context7_enabled() -> bool:
    if os.getenv("MCP_CONTEXT7_ENABLED", "1").lower() in ("0", "false", "no"):
        return False
    return True


def detect_libraries(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for pattern, name in _LIBRARY_HINTS:
        if pattern.search(text) and name not in seen:
            seen.add(name)
            found.append(name)
    return found[:MAX_QUERY_DOCS]


def _extract_library_id(resolve_text: str) -> str | None:
    match = re.search(r"(/[\w.-]+/[\w.-]+(?:/[\w.-]+)?)", resolve_text)
    if match:
        return match.group(1)
    return None


async def enrich_with_context7(
    issue: str,
    *,
    client: McpToolClient | None = None,
) -> tuple[str, list[str]]:
    """Fetch Context7 docs for libraries mentioned in the issue.

    Returns:
        (context_block, tools_used) — empty context if disabled or unavailable.
    """
    if not _context7_enabled():
        return "", []

    mcp = client or McpToolClient()
    if not mcp.has_server(CONTEXT7_SERVER):
        return "", []

    libraries = detect_libraries(issue)
    if not libraries:
        return "", []

    tools_used: list[str] = []
    sections: list[str] = ["=== MCP Context7 Documentation ==="]

    for lib_name in libraries:
        try:
            resolve_args = {
                "libraryName": lib_name,
                "query": issue[:500],
            }
            resolve_out = await mcp.call_tool(
                CONTEXT7_SERVER,
                "resolve-library-id",
                resolve_args,
            )
            tools_used.append(f"{CONTEXT7_SERVER}/resolve-library-id")
            library_id = _extract_library_id(resolve_out)
            if not library_id:
                continue

            docs_out = await mcp.call_tool(
                CONTEXT7_SERVER,
                "query-docs",
                {
                    "libraryId": library_id,
                    "query": issue[:800],
                },
            )
            tools_used.append(f"{CONTEXT7_SERVER}/query-docs")
            sections.append(f"\n### {lib_name} ({library_id})\n{docs_out[:4000]}")
        except (ValueError, OSError, RuntimeError) as exc:
            sections.append(f"\n### {lib_name}\n(Context7 unavailable: {exc})")

    sections.append("\n=== End Context7 Documentation ===")
    if len(sections) <= 2:
        return "", tools_used
    return "\n".join(sections), tools_used


def enrich_with_context7_sync(
    issue: str,
    *,
    client: McpToolClient | None = None,
) -> tuple[str, list[str]]:
    import asyncio

    return asyncio.run(enrich_with_context7(issue, client=client))
