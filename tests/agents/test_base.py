"""Tests for base agent class."""

import pytest

from agent_graph.agents.base import BaseAgent
from agent_graph.state import TaskState


class ConcreteAgent(BaseAgent):
    """Concrete implementation of BaseAgent for testing."""

    name = "test"

    def _execute(self, state: TaskState) -> dict:
        return {"plan": "test plan"}


class FailingAgent(BaseAgent):
    """Agent that raises an exception."""

    name = "failing"

    def _execute(self, state: TaskState) -> dict:
        raise ValueError("intentional failure")


def test_concrete_agent_runs():
    agent = ConcreteAgent()
    state: TaskState = {"issue": "test", "plan": "", "implementation_result": "", "verification_result": "", "target_repo_path": "", "work_repo_path": "", "diff_patch": "", "github_issue_url": "", "pr_url": "", "pr_error": ""}
    result = agent.run(state)
    assert result == {"plan": "test plan"}


def test_failing_agent_raises():
    agent = FailingAgent()
    state: TaskState = {"issue": "test", "plan": "", "implementation_result": "", "verification_result": "", "target_repo_path": "", "work_repo_path": "", "diff_patch": "", "github_issue_url": "", "pr_url": "", "pr_error": ""}
    with pytest.raises(RuntimeError, match="intentional failure"):
        agent.run(state)
