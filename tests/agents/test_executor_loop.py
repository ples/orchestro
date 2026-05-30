"""Tests for ExecutorLoopAgent."""

from unittest.mock import MagicMock, patch

from agent_graph.agents.executor_loop import (
    ExecutorLoopAgent,
    _execute_repo_with_retry,
    _repo_work_path,
    _run_single,
)
from agent_graph.models import ExecutionResult
from agent_graph.pr_skip import NO_CHANGES_SKIP, REQUIRED_CHANGES_MISSING
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
        "change_stat": "1 file changed",
        "pr_error": "",
        "pr_skip_reason": "",
    }

    state = _make_state(
        target_repos=[
            {
                "target_repo_path": "https://bitbucket.org/team/admin-ui.git",
                "planner_clone_path": "/tmp/planner_repo_abc/admin-ui",
                "repo_baseline_sha": "sha1",
                "repo_summary": "## Findings\nBug in component",
                "requires_changes": True,
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
        scoped_plan="plan",
        repo_record=record,
        existing_repo_path=record["planner_clone_path"],
        baseline_sha=record["repo_baseline_sha"],
        skip_health_checks=True,
        force_openhands=True,
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
        scoped_plan="plan",
        repo_record=record,
        existing_repo_path=record["planner_clone_path"],
        baseline_sha=record["repo_baseline_sha"],
        skip_health_checks=True,
        force_openhands=True,
    )

    assert updated.get("pr_skip_reason") == NO_CHANGES_SKIP
    assert updated.get("pr_error", "") == ""


@patch("agent_graph.agents.executor_loop._run_single")
def test_execute_repo_with_retry_fails_after_two_no_ops(mock_run_single):
    no_op = {
        "target_repo_path": "https://bitbucket.org/team/admin-ui.git",
        "pr_skip_reason": REQUIRED_CHANGES_MISSING,
        "pr_error": "",
        "repo_summary": "No file changes",
    }
    mock_run_single.side_effect = [no_op, no_op]

    record = {
        "target_repo_path": "https://bitbucket.org/team/admin-ui.git",
        "requires_changes": True,
        "expected_targets": ["src/components/GroupDetails.tsx"],
    }
    updated = _execute_repo_with_retry(
        target_repo=record["target_repo_path"],
        issue="Fix bug",
        scoped_plan="plan",
        repo_record=record,
        skip_health_checks=True,
    )
    assert mock_run_single.call_count == 2
    assert "Required changes missing" in updated.get("pr_error", "")
    assert updated.get("pr_skip_reason", "") == ""
    assert updated.get("execution_diagnostics")


@patch("agent_graph.agents.executor_loop._run_single")
def test_execute_repo_with_retry_succeeds_on_second_attempt(mock_run_single):
    no_op = {
        "target_repo_path": "https://bitbucket.org/team/admin-ui.git",
        "pr_skip_reason": REQUIRED_CHANGES_MISSING,
        "change_stat": "",
    }
    success = {
        "target_repo_path": "https://bitbucket.org/team/admin-ui.git",
        "change_stat": "1 file changed",
        "pr_error": "",
        "pr_skip_reason": "",
    }
    mock_run_single.side_effect = [no_op, success]

    record = {
        "target_repo_path": "https://bitbucket.org/team/admin-ui.git",
        "requires_changes": True,
    }
    updated = _execute_repo_with_retry(
        target_repo=record["target_repo_path"],
        issue="Fix bug",
        scoped_plan="plan",
        repo_record=record,
        skip_health_checks=True,
    )
    assert mock_run_single.call_count == 2
    assert updated.get("change_stat") == "1 file changed"


@patch("agent_graph.openhands_client.OpenHandsClient.run_task")
def test_run_single_required_no_changes_marks_retryable(mock_run_task):
    mock_run_task.return_value = ExecutionResult(
        success=True,
        no_changes=True,
        summary="No file changes in admin-ui",
        work_repo_path="/tmp/repo",
        repo_baseline_sha="sha1",
    )
    record = {
        "target_repo_path": "https://bitbucket.org/team/admin-ui.git",
        "requires_changes": True,
        "expected_targets": ["GroupDetails.tsx"],
    }
    updated = _run_single(
        target_repo=record["target_repo_path"],
        issue="Fix",
        scoped_plan="plan",
        repo_record=record,
        skip_health_checks=True,
        force_openhands=True,
    )
    assert updated.get("pr_skip_reason") == REQUIRED_CHANGES_MISSING
    assert updated.get("execution_diagnostics")


@patch("agent_graph.openhands_client.OpenHandsClient.check_runtime_ready", return_value="Docker down")
@patch("agent_graph.agents.executor_loop._run_single")
def test_executor_skips_when_runtime_unavailable(mock_run_single, _mock_health):
    state = _make_state(
        target_repos=[
            {
                "target_repo_path": "https://bitbucket.org/team/admin-ui.git",
                "planner_clone_path": "/tmp/repo",
                "requires_changes": True,
            }
        ]
    )
    result = ExecutorLoopAgent().run(state)
    mock_run_single.assert_not_called()
    assert "Docker down" in result["target_repos"][0].get("pr_error", "")


def test_repo_work_path_follow_up_prefers_work_tree():
    record = {
        "work_repo_path": "/tmp/work",
        "planner_clone_path": "/tmp/planner",
    }
    assert _repo_work_path(record, follow_up=True) == "/tmp/work"
    assert _repo_work_path(record, follow_up=False) == "/tmp/planner"


@patch("agent_graph.openhands_client.OpenHandsClient.check_runtime_ready", return_value=None)
@patch("agent_graph.agents.executor_loop._run_single")
def test_executor_loop_follow_up_uses_work_path(mock_run_single, _mock_health):
    mock_run_single.return_value = {
        "target_repo_path": "https://github.com/o/r.git",
        "work_repo_path": "/tmp/work",
    }
    state = _make_state(
        workflow_mode="follow_up",
        follow_up_prompt="Make button blue",
        target_repos=[
            {
                "target_repo_path": "https://github.com/o/r.git",
                "planner_clone_path": "/tmp/planner",
                "work_repo_path": "/tmp/work",
                "repo_baseline_sha": "sha1",
                "requires_changes": True,
            }
        ],
    )
    result = ExecutorLoopAgent().run(state)
    call_kw = mock_run_single.call_args[1]
    assert call_kw["existing_repo_path"] == "/tmp/work"
    assert call_kw["follow_up"] is True
    assert call_kw["follow_up_prompt"] == "Make button blue"
    assert result.get("iteration") == 1


@patch("agent_graph.openhands_client.OpenHandsClient.check_runtime_ready", return_value=None)
@patch("agent_graph.agents.executor_loop._run_single")
def test_executor_skips_repo_without_required_changes(mock_run_single, _mock_health):
    state = _make_state(
        target_repos=[
            {
                "target_repo_path": "https://bitbucket.org/dmetrics/identity-hub-api.git",
                "planner_clone_path": "/tmp/identity-hub-api",
                "requires_changes": False,
            },
            {
                "target_repo_path": "https://bitbucket.org/dmetrics/admin-ui.git",
                "planner_clone_path": "/tmp/admin-ui",
                "requires_changes": True,
            },
        ]
    )
    mock_run_single.return_value = {
        "work_repo_path": "/tmp/admin-ui",
        "change_stat": "1 file changed",
        "pr_error": "",
        "pr_skip_reason": "",
    }
    result = ExecutorLoopAgent().run(state)
    mock_run_single.assert_called_once()
    assert result["target_repos"][0]["pr_skip_reason"] == NO_CHANGES_SKIP
    assert result["target_repos"][0].get("execution_mode") == "skip"
