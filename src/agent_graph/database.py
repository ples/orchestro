"""Async PostgreSQL persistence for workflow runs and LLM-powered Q&A."""

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg

from agent_graph.state import TaskState

logger = logging.getLogger(__name__)


_DDL = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    issue TEXT NOT NULL,
    plan TEXT,
    implementation_result TEXT,
    verification_result TEXT,
    pr_url TEXT,
    pr_error TEXT,
    pr_skip_reason TEXT,
    workflow_node TEXT NOT NULL DEFAULT 'idle',
    workflow_mode TEXT NOT NULL DEFAULT 'initial',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    state_json JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_chat_id ON workflow_runs (chat_id, updated_at DESC);
"""


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _build_db_url() -> str:
    direct = os.getenv("RUNS_DB_URL", "").strip()
    if direct:
        return direct
    host = os.getenv("POSTGRES_HOST", "127.0.0.1").strip()
    port = os.getenv("POSTGRES_PORT", "5432").strip()
    db = os.getenv("POSTGRES_DB", "agent_graph").strip()
    user = os.getenv("POSTGRES_USER", "agent_graph").strip()
    password = os.getenv("POSTGRES_PASSWORD", "agent_graph").strip()
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _state_to_row(run_id: str, chat_id: str, state: TaskState) -> dict[str, Any]:
    now = _now_utc()
    return {
        "run_id": run_id,
        "chat_id": chat_id,
        "issue": (state.get("issue") or "")[:2000],
        "plan": (state.get("plan") or "")[:10000],
        "implementation_result": (state.get("implementation_result") or "")[:4000],
        "verification_result": (state.get("verification_result") or "")[:4000],
        "pr_url": state.get("pr_url") or "",
        "pr_error": state.get("pr_error") or "",
        "pr_skip_reason": state.get("pr_skip_reason") or "",
        "workflow_node": state.get("workflow_node") or "idle",
        "workflow_mode": state.get("workflow_mode") or "initial",
        "created_at": now,
        "updated_at": now,
        "state_json": json.dumps(dict(state), default=str),
    }


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("created_at", "updated_at"):
        value = row.get(key)
        if isinstance(value, datetime):
            row[key] = value.isoformat(timespec="seconds")
    state_json = row.get("state_json")
    if isinstance(state_json, dict):
        row["state_json"] = json.dumps(state_json, default=str)
    return row


class RunDatabase:
    """Lightweight async PostgreSQL store for workflow run states."""

    def __init__(self, db_url: str | None = None) -> None:
        self.db_url = db_url or _build_db_url()
        self._pool: asyncpg.Pool | None = None

    async def _ensure_pool(self) -> asyncpg.Pool:
        current_loop = asyncio.get_running_loop()
        pool_loop = getattr(self._pool, "_loop", None) if self._pool is not None else None
        if self._pool is not None and pool_loop is not current_loop:
            # Pool objects are event-loop-bound; recreate lazily on loop switch.
            self._pool = None
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                dsn=self.db_url,
                min_size=1,
                max_size=5,
            )
        return self._pool

    async def init(self) -> None:
        """Create tables if they don't exist yet."""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(_DDL)
        logger.debug("RunDatabase initialized at %s", self.db_url)

    async def save_run(self, run_id: str, chat_id: str, state: TaskState) -> None:
        """Insert or update a workflow run record."""
        row = _state_to_row(run_id, chat_id, state)
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO workflow_runs
                    (run_id, chat_id, issue, plan, implementation_result,
                     verification_result, pr_url, pr_error, pr_skip_reason,
                     workflow_node, workflow_mode, created_at, updated_at, state_json)
                VALUES
                    ($1, $2, $3, $4, $5,
                     $6, $7, $8, $9,
                     $10, $11, $12, $13, $14::jsonb)
                ON CONFLICT (run_id) DO UPDATE SET
                    chat_id = EXCLUDED.chat_id,
                    issue = EXCLUDED.issue,
                    plan = EXCLUDED.plan,
                    implementation_result = EXCLUDED.implementation_result,
                    verification_result = EXCLUDED.verification_result,
                    pr_url = EXCLUDED.pr_url,
                    pr_error = EXCLUDED.pr_error,
                    pr_skip_reason = EXCLUDED.pr_skip_reason,
                    workflow_node = EXCLUDED.workflow_node,
                    workflow_mode = EXCLUDED.workflow_mode,
                    updated_at = EXCLUDED.updated_at,
                    state_json = EXCLUDED.state_json
                """,
                row["run_id"],
                row["chat_id"],
                row["issue"],
                row["plan"],
                row["implementation_result"],
                row["verification_result"],
                row["pr_url"],
                row["pr_error"],
                row["pr_skip_reason"],
                row["workflow_node"],
                row["workflow_mode"],
                row["created_at"],
                row["updated_at"],
                row["state_json"],
            )
        logger.debug("Saved run %s (chat=%s, node=%s)", run_id, chat_id, row["workflow_node"])

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Return a single run record, or None if not found."""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM workflow_runs WHERE run_id = $1",
                run_id,
            )
        if row is None:
            return None
        return _normalize_row(dict(row))

    async def list_runs(
        self,
        chat_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return the most recent runs, optionally filtered by chat_id."""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            if chat_id:
                rows = await conn.fetch(
                    "SELECT * FROM workflow_runs WHERE chat_id = $1 ORDER BY updated_at DESC LIMIT $2",
                    chat_id,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    "SELECT * FROM workflow_runs ORDER BY updated_at DESC LIMIT $1",
                    limit,
                )
        return [_normalize_row(dict(r)) for r in rows]

    async def ask_question(self, run: dict[str, Any], question: str) -> str:
        """Use the local LLM to answer a question about a specific run."""
        issue = run.get("issue", "")
        plan = run.get("plan", "")
        impl = run.get("implementation_result", "")
        verif = run.get("verification_result", "")
        pr_url = run.get("pr_url", "")
        node = run.get("workflow_node", "")
        created = run.get("created_at", "")

        system = (
            "You are a helpful engineering assistant with access to a completed "
            "workflow run. Answer the user's question about the issue, plan, or "
            "implementation details provided in the context. Be concise and precise."
        )

        context_parts = [f"## Issue\n{issue}"]
        if plan:
            context_parts.append(f"## Plan\n{plan[:3000]}")
        if impl:
            context_parts.append(f"## Implementation Result\n{impl[:2000]}")
        if verif:
            context_parts.append(f"## Verification Result\n{verif[:1000]}")
        if pr_url:
            context_parts.append(f"## PR URL\n{pr_url}")
        context_parts.append(f"## Workflow Status\nNode: {node} | Created: {created}")

        context = "\n\n".join(context_parts)
        user_message = f"{context}\n\n---\n\nQuestion: {question}"

        from agent_graph.llm_client import chat_completion

        answer = chat_completion(system, user_message, max_tokens=1024, temperature=0.2)
        if answer:
            return answer
        return (
            "⚠️ Could not reach the LLM to answer your question.\n\n"
            f"**Stored context:**\n{context[:800]}"
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


_db: RunDatabase | None = None


def get_db(db_url: str | None = None) -> RunDatabase:
    """Return (and lazily create) the module-level RunDatabase singleton."""
    global _db
    if _db is None:
        _db = RunDatabase(db_url)
    return _db


def new_run_id() -> str:
    """Generate a unique run ID."""
    return str(uuid.uuid4())


def load_run_state(run: dict[str, Any]) -> TaskState:
    """Deserialize TaskState from a workflow_runs row."""
    raw = run.get("state_json") or "{}"
    if isinstance(raw, str):
        return json.loads(raw)
    return dict(raw)
