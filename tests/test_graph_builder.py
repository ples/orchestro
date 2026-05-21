"""Tests for graph builder."""

from unittest.mock import patch

from agent_graph.graph_builder import _pr_router, build_graph
from agent_graph.state import TaskState


def test_pr_router_done_with_url():
    state: TaskState = {
        "issue": "test",
        "plan": "plan",
        "implementation_result": "done",
        "verification_result": "passed",
        "pr_url": "https://github.com/o/r/pull/1",
        "pr_error": "",
    }
    assert _pr_router(state) == "done"


def test_pr_router_done_no_pr():
    state: TaskState = {
        "issue": "test",
        "pr_url": "",
        "pr_error": "",
    }
    assert _pr_router(state) == "done"


def test_pr_router_failed():
    state: TaskState = {
        "issue": "test",
        "pr_url": "",
        "pr_error": "push failed",
    }
    assert _pr_router(state) == "failed"


def test_build_graph_compiles():
    def mock_pr_creator(_state: dict) -> dict:
        return {"pr_url": "https://github.com/o/r/pull/1", "pr_error": ""}

    def mock_executor_loop(_state: dict) -> dict:
        return {"target_repos": []}

    graph = build_graph(
        executor_loop_fn=mock_executor_loop,
        pr_creator_fn=mock_pr_creator,
    )
    assert graph is not None
    nodes = [k for k in graph.nodes.keys() if not k.startswith("__")]
    assert "planner" in nodes
    assert "executor_loop" in nodes
    assert "pr_aggregator" in nodes

    state: TaskState = {
        "issue": "add feature X",
        "plan": "plan",
        "implementation_result": "",
        "verification_result": "",
        "target_repo_path": "https://github.com/o/r.git",
        "work_repo_path": "",
        "diff_patch": "",
        "github_issue_url": "",
        "pr_url": "",
        "pr_error": "",
        "target_repos": [{"target_repo_path": "https://github.com/o/r.git"}],
    }

    result = graph.invoke(state)
    assert result["pr_url"] == "https://github.com/o/r/pull/1"
