"""Tests for TaskState serialization."""

import json
from pathlib import Path

from agent_graph.state_io import load_task_state, save_task_state


def test_save_and_load_roundtrip(tmp_path: Path):
    path = tmp_path / "state.json"
    state = {
        "issue": "Fix bug",
        "plan": "1. Do thing",
        "iteration": 0,
        "target_repos": [{"target_repo_path": "https://github.com/o/r.git"}],
    }
    save_task_state(path, state)
    loaded = load_task_state(path)
    assert loaded["issue"] == "Fix bug"
    assert loaded["iteration"] == 0
    assert loaded["target_repos"][0]["target_repo_path"] == "https://github.com/o/r.git"
    raw = json.loads(path.read_text())
    assert raw["plan"] == "1. Do thing"
