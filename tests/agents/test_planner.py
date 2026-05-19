"""Tests for PlannerAgent."""

from agent_graph.agents.planner import PlannerAgent
from agent_graph.state import TaskState


def test_planner_returns_plan():
    agent = PlannerAgent()
    state: TaskState = {
        "issue": "Add logout endpoint",
        "plan": "",
        "implementation_result": "",
        "verification_result": "",
        "pr_url": "",
        "pr_error": "",
        "target_repo_path": "",
        "diff_patch": "",
        "github_issue_url": "",
    }
    result = agent.run(state)
    assert "plan" in result
    assert "Add logout endpoint" in result["plan"]


def test_planner_with_different_issue():
    agent = PlannerAgent()
    state: TaskState = {
        "issue": "Fix database migration",
        "plan": "",
        "implementation_result": "",
        "verification_result": "",
        "pr_url": "",
        "pr_error": "",
        "target_repo_path": "",
        "diff_patch": "",
        "github_issue_url": "",
    }
    result = agent.run(state)
    assert "Fix database migration" in result["plan"]
