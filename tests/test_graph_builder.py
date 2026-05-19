"""Tests for graph builder."""

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
    def mock_pr_creator(state: dict) -> dict:
        return {"pr_url": "https://github.com/o/r/pull/1", "pr_error": ""}

    graph = build_graph(pr_creator_fn=mock_pr_creator)
    assert graph is not None

    state: TaskState = {
        "issue": "add feature X",
        "plan": "",
        "implementation_result": "",
        "verification_result": "",
        "target_repo_path": "https://github.com/o/r.git",
        "work_repo_path": "",
        "diff_patch": "",
        "github_issue_url": "",
        "pr_url": "",
        "pr_error": "",
    }

    result = graph.invoke(state)
    assert result["pr_url"] == "https://github.com/o/r/pull/1"
