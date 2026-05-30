"""Tests for PostgreSQL run persistence."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from agent_graph.database import RunDatabase, load_run_state, new_run_id
from agent_graph.state import TaskState


class FakeConnection:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.ddl_executed = False

    async def execute(self, query: str, *args):
        if "CREATE TABLE IF NOT EXISTS workflow_runs" in query:
            self.ddl_executed = True
            return "OK"
        if "INSERT INTO workflow_runs" in query:
            row = {
                "run_id": args[0],
                "chat_id": args[1],
                "issue": args[2],
                "plan": args[3],
                "implementation_result": args[4],
                "verification_result": args[5],
                "pr_url": args[6],
                "pr_error": args[7],
                "pr_skip_reason": args[8],
                "workflow_node": args[9],
                "workflow_mode": args[10],
                "created_at": args[11],
                "updated_at": args[12],
                "state_json": args[13],
            }
            existing = self.rows.get(row["run_id"])
            if existing:
                row["created_at"] = existing["created_at"]
            self.rows[row["run_id"]] = row
            return "UPSERT 1"
        return "OK"

    async def fetchrow(self, query: str, *args):
        if "WHERE run_id = $1" in query:
            return self.rows.get(args[0])
        return None

    async def fetch(self, query: str, *args):
        rows = list(self.rows.values())
        if "WHERE chat_id = $1" in query:
            chat_id = args[0]
            rows = [r for r in rows if r["chat_id"] == chat_id]
            limit = args[1]
        else:
            limit = args[0]
        rows = sorted(rows, key=lambda r: r["updated_at"], reverse=True)
        return rows[:limit]


class FakePool:
    def __init__(self, conn: FakeConnection):
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):
        yield self.conn

    async def close(self):
        return None


@pytest.fixture
async def db():
    conn = FakeConnection()
    pool = FakePool(conn)
    async def _fake_create_pool(**_kwargs):
        return pool

    with patch("agent_graph.database.asyncpg.create_pool", side_effect=_fake_create_pool):
        database = RunDatabase("postgresql://test:test@localhost:5432/test")
        await database.init()
        yield database, conn


@pytest.mark.asyncio
async def test_init_creates_table(db):
    _database, conn = db
    assert conn.ddl_executed is True


@pytest.mark.asyncio
async def test_save_and_get_run(db):
    database, _conn = db
    state: TaskState = {
        "issue": "Fix login bug",
        "plan": "1. Patch auth",
        "workflow_node": "completed",
        "workflow_mode": "initial",
        "pr_url": "https://github.com/o/r/pull/1",
    }
    run_id = new_run_id()
    await database.save_run(run_id, "cli", state)

    loaded = await database.get_run(run_id)
    assert loaded is not None
    assert loaded["run_id"] == run_id
    assert loaded["chat_id"] == "cli"
    assert loaded["issue"] == "Fix login bug"
    assert loaded["plan"] == "1. Patch auth"
    assert loaded["workflow_node"] == "completed"
    assert loaded["pr_url"] == "https://github.com/o/r/pull/1"


@pytest.mark.asyncio
async def test_save_preserves_created_at(db):
    database, _conn = db
    state: TaskState = {"issue": "First", "workflow_node": "planning"}
    run_id = new_run_id()
    await database.save_run(run_id, "123", state)

    first = await database.get_run(run_id)
    created_at = first["created_at"]

    state["workflow_node"] = "completed"
    state["plan"] = "Done"
    await database.save_run(run_id, "123", state)

    second = await database.get_run(run_id)
    assert second["created_at"] == created_at
    assert second["plan"] == "Done"


@pytest.mark.asyncio
async def test_list_runs_filtered_by_chat(db):
    database, conn = db
    ts = datetime.now(timezone.utc)
    conn.rows["r1"] = {
        "run_id": "r1",
        "chat_id": "cli",
        "issue": "A",
        "plan": "",
        "implementation_result": "",
        "verification_result": "",
        "pr_url": "",
        "pr_error": "",
        "pr_skip_reason": "",
        "workflow_node": "completed",
        "workflow_mode": "initial",
        "created_at": ts,
        "updated_at": ts,
        "state_json": "{}",
    }
    conn.rows["r2"] = {**conn.rows["r1"], "run_id": "r2", "chat_id": "999", "issue": "B"}
    conn.rows["r3"] = {**conn.rows["r1"], "run_id": "r3", "issue": "C", "workflow_node": "idle"}

    cli_runs = await database.list_runs(chat_id="cli", limit=10)
    assert len(cli_runs) == 2
    assert {r["run_id"] for r in cli_runs} == {"r1", "r3"}

    all_runs = await database.list_runs(limit=10)
    assert len(all_runs) == 3


@pytest.mark.asyncio
async def test_get_run_missing(db):
    database, _conn = db
    assert await database.get_run("nonexistent") is None


@pytest.mark.asyncio
async def test_load_run_state(db):
    database, _conn = db
    state: TaskState = {
        "issue": "Fix bug",
        "plan": "Patch it",
        "workflow_node": "completed",
        "iteration": 0,
    }
    run_id = new_run_id()
    await database.save_run(run_id, "cli", state)
    row = await database.get_run(run_id)
    loaded = load_run_state(row)
    assert loaded["issue"] == "Fix bug"
    assert loaded["plan"] == "Patch it"


@pytest.mark.asyncio
async def test_ask_question_uses_llm(db):
    database, _conn = db
    run = {
        "issue": "Add caching",
        "plan": "Use Redis",
        "implementation_result": "Added redis client",
        "verification_result": "Tests pass",
        "pr_url": "",
        "workflow_node": "completed",
        "created_at": "2026-01-01T00:00:00",
    }
    with (
        patch(
            "agent_graph.llm_client.chat_completion",
            return_value="Redis was added for caching.",
        ),
    ):
        answer = await database.ask_question(run, "What was implemented?")

    assert "Redis" in answer
