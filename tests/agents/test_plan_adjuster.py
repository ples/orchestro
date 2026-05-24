"""Tests for PlanAdjusterAgent."""

from unittest.mock import patch

import pytest

from agent_graph.agents.plan_adjuster import PlanAdjusterAgent
from agent_graph.state import TaskState


def _state(**kwargs) -> TaskState:
    base: TaskState = {
        "issue": "Fix button color",
        "plan": "1. Update CSS",
        "follow_up_prompt": "Use blue instead of red",
        "workflow_mode": "follow_up",
        "iteration": 0,
        "target_repos": [
            {
                "target_repo_path": "https://github.com/o/r.git",
                "work_repo_path": "/tmp/work",
                "change_stat": "1 file changed",
            }
        ],
    }
    base.update(kwargs)
    return base


@patch("agent_graph.agents.plan_adjuster.OpenHandsClient")
def test_plan_adjuster_builds_plan(mock_client_cls):
    mock_client_cls.return_value._plan_with_local_llm.return_value = (
        "## Implementation steps\n1. Change color to blue"
    )
    result = PlanAdjusterAgent().run(_state())
    assert "blue" in result["plan"].lower()
    assert result["pr_url"] == ""
    assert result["workflow_mode"] == "follow_up"


@patch("agent_graph.agents.plan_adjuster.OpenHandsClient")
def test_plan_adjuster_fallback_without_llm(mock_client_cls):
    mock_client_cls.return_value._plan_with_local_llm.return_value = ""
    result = PlanAdjusterAgent().run(_state())
    assert "Adjustment instructions" in result["plan"]
    assert "Use blue instead of red" in result["plan"]


def test_plan_adjuster_rejects_missing_follow_up():
    with pytest.raises(RuntimeError, match="follow_up_prompt"):
        PlanAdjusterAgent().run(_state(follow_up_prompt=""))


def test_plan_adjuster_rejects_iteration_limit():
    with pytest.raises(RuntimeError, match="Follow-up limit"):
        PlanAdjusterAgent().run(_state(iteration=1))


def test_plan_adjuster_requires_work_tree():
    with pytest.raises(RuntimeError, match="No work tree"):
        PlanAdjusterAgent().run(
            _state(
                target_repos=[{"target_repo_path": "https://github.com/o/r.git"}]
            )
        )
