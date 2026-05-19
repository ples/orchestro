"""Tests for ExecutorAgent."""

from unittest.mock import MagicMock, patch

import pytest

from agent_graph.agents.executor import ExecutorAgent
from agent_graph.models import ExecutionResult
from agent_graph.state import TaskState


def test_executor_delegates_to_openhands():
    mock_result = ExecutionResult(
        success=True,
        summary="Feature implemented successfully",
        work_repo_path="/tmp/work",
        diff_patch="diff --git a/foo",
    )

    with patch(
        "agent_graph.openhands_client.OpenHandsClient"
    ) as mock_cls:
        mock_cls.return_value.run_task.return_value = mock_result

        agent = ExecutorAgent()
        plan = """
1. Write code
2. Run tests
"""
        state: TaskState = {
            "issue": "Add a new endpoint",
            "plan": plan,
            "implementation_result": "",
            "verification_result": "",
            "target_repo_path": "/tmp/test-repo",
            "diff_patch": "",
            "work_repo_path": "",
        }
        result = agent.run(state)

        mock_cls.return_value.run_task.assert_called_once_with(
            target_repo="/tmp/test-repo",
            issue="Add a new endpoint",
            plan=plan,
        )
        assert result["implementation_result"] == (
            "Feature implemented successfully"
        )
        assert result["work_repo_path"] == "/tmp/work"
        assert result["diff_patch"] == "diff --git a/foo"


def test_executor_with_no_target_repo():
    """When no target repo is provided, executor should still call OpenHands
    and pass an empty string so OpenHands can handle repo cloning internally."""
    mock_result = ExecutionResult(
        success=True,
        summary="Task completed via OpenHands workspace",
    )

    with patch.dict("os.environ", {}, clear=True):
        with patch(
            "agent_graph.openhands_client.OpenHandsClient"
        ) as mock_cls:
            mock_cls.return_value.run_task.return_value = mock_result

            agent = ExecutorAgent()
            state: TaskState = {
                "issue": "Do something",
                "plan": "",
                "implementation_result": "",
                "verification_result": "",
                "target_repo_path": "",
                "diff_patch": "",
                "work_repo_path": "",
                "github_issue_url": "",
            }

            result = agent.run(state)
            mock_cls.return_value.run_task.assert_called_once_with(
                target_repo="",
                issue="Do something",
                plan="",
            )
            assert result["implementation_result"] == "Task completed via OpenHands workspace"


def test_executor_handles_failure():
    mock_result = ExecutionResult(
        success=False,
        summary="OpenHands could not implement the feature",
    )

    with patch(
        "agent_graph.openhands_client.OpenHandsClient"
    ) as mock_cls:
        mock_cls.return_value.run_task.return_value = mock_result

        agent = ExecutorAgent()
        state: TaskState = {
            "issue": "Impossible task",
            "plan": "Step 1...",
            "implementation_result": "",
            "verification_result": "",
            "target_repo_path": "/tmp/test-repo",
            "diff_patch": "",
            "work_repo_path": "",
        }

        try:
            agent.run(state)
        except RuntimeError:
            pass
        else:
            assert False, "Expected RuntimeError on failure"


def test_executor_respects_env_target_repo():
    mock_result = ExecutionResult(
        success=True,
        summary="Env-selected repo updated",
    )

    with patch(
        "agent_graph.openhands_client.OpenHandsClient"
    ) as mock_cls:
        mock_cls.return_value.run_task.return_value = mock_result

        agent = ExecutorAgent()
        state: TaskState = {
            "issue": "Fix bug",
            "plan": "Fix it.",
            "implementation_result": "",
            "verification_result": "",
            "target_repo_path": "",
            "diff_patch": "",
            "work_repo_path": "",
        }

        with patch.dict(
            "os.environ", {"TARGET_REPO_PATH": "/from/env/repo"}
        ):
            result = agent.run(state)
            mock_cls.return_value.run_task.assert_called_once_with(
                target_repo="/from/env/repo",
                issue="Fix bug",
                plan="Fix it.",
            )
            assert result["implementation_result"] == (
                "Env-selected repo updated"
            )
