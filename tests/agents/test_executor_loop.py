"""Tests for ExecutorLoopAgent."""

from unittest.mock import MagicMock, patch

from agent_graph.agents.executor_loop import ExecutorLoopAgent, _run_single
from agent_graph.models import ExecutionResult
from agent_graph.state import TaskState


def _make_state(**kwargs) -> TaskState:
    state: TaskState = {
        "issue": "Fix bug",
        "plan": "=== PLAN ===\nDo the fix",
        "target_repos": [],
    }
    state.update(kwargs)
    return state


@patch("agent_graph.openhands_client.OpenHandsClient.check_runtime_ready", return_value=None)
@patch("agent_graph.agents.executor_loop._run_single")
def test_executor_loop_passes_planner_clone(mock_run_single, _mock_health):
    mock_run_single.return_value = {
        "target_repo_path": "https://bitbucket.org/team/admin-ui.git",
        "planner_clone_path": "/tmp/planner_repo_abc/admin-ui",
        "repo_baseline_sha": "sha1",
        "work_repo_path": "/tmp/planner_repo_abc/admin-ui",
    }

    state = _make_state(
        target_repos=[
            {
                "target_repo_path": "https://bitbucket.org/team/admin-ui.git",
                "planner_clone_path": "/tmp/planner_repo_abc/admin-ui",
                "repo_baseline_sha": "sha1",
                "repo_summary": "## Findings\nBug in component",
            }
        ]
    )

    result = ExecutorLoopAgent().run(state)
    assert len(result["target_repos"]) == 1
    mock_run_single.assert_called_once()
    call_kw = mock_run_single.call_args[1]
    assert call_kw["existing_repo_path"] == "/tmp/planner_repo_abc/admin-ui"
    assert call_kw["baseline_sha"] == "sha1"


@patch("agent_graph.openhands_client.OpenHandsClient.run_task")
def test_run_single_reuses_existing_path(mock_run_task):
    mock_run_task.return_value = ExecutionResult(
        success=True,
        summary="done",
        work_repo_path="/tmp/planner_repo_abc/admin-ui",
        repo_baseline_sha="sha1",
    )

    record = {
        "target_repo_path": "https://bitbucket.org/team/admin-ui.git",
        "planner_clone_path": "/tmp/planner_repo_abc/admin-ui",
        "repo_baseline_sha": "sha1",
    }

    updated = _run_single(
        target_repo=record["target_repo_path"],
        issue="Fix bug",
        plan="plan",
        repo_record=record,
        existing_repo_path=record["planner_clone_path"],
        baseline_sha=record["repo_baseline_sha"],
        skip_health_checks=True,
    )

    mock_run_task.assert_called_once()
    assert mock_run_task.call_args[1]["existing_repo_path"] == "/tmp/planner_repo_abc/admin-ui"
    assert mock_run_task.call_args[1]["baseline_sha"] == "sha1"
    assert updated["work_repo_path"] == "/tmp/planner_repo_abc/admin-ui"


@patch("agent_graph.openhands_client.OpenHandsClient.run_task")
def test_run_single_no_changes_sets_skip_not_error(mock_run_task):
    mock_run_task.return_value = ExecutionResult(
        success=True,
        no_changes=True,
        summary="No file changes in admin-ui — OpenHands finished without editing the repo",
        work_repo_path="/tmp/planner_repo_abc/admin-ui",
        repo_baseline_sha="sha1",
    )

    record = {
        "target_repo_path": "https://bitbucket.org/team/admin-ui.git",
        "planner_clone_path": "/tmp/planner_repo_abc/admin-ui",
        "repo_baseline_sha": "sha1",
    }

    updated = _run_single(
        target_repo=record["target_repo_path"],
        issue="Fix bug",
        plan="plan",
        repo_record=record,
        existing_repo_path=record["planner_clone_path"],
        baseline_sha=record["repo_baseline_sha"],
        skip_health_checks=True,
    )

    assert updated.get("pr_skip_reason") == "no_changes"
    assert updated.get("pr_error", "") == ""


@patch("agent_graph.openhands_client.OpenHandsClient.check_runtime_ready", return_value="Docker down")
@patch("agent_graph.agents.executor_loop._run_single")
def test_executor_skips_when_runtime_unavailable(mock_run_single, _mock_health):
    state = _make_state(
        target_repos=[
            {
                "target_repo_path": "https://bitbucket.org/team/admin-ui.git",
                "planner_clone_path": "/tmp/repo",
            }
        ]
    )
    result = ExecutorLoopAgent().run(state)
    mock_run_single.assert_not_called()
    assert "Docker down" in result["target_repos"][0].get("pr_error", "")
