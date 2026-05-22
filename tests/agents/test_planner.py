"""Tests for PlannerAgent."""

from unittest.mock import MagicMock, patch

from agent_graph.agents.planner import PlannerAgent
from agent_graph.models import PlanningResult
from agent_graph.state import TaskState


def _make_state(**kwargs) -> TaskState:
    state: TaskState = {
        "issue": "Add logout endpoint",
        "plan": "",
        "target_repos": [],
    }
    state.update(kwargs)
    return state


def test_planner_returns_plan_without_repos():
    agent = PlannerAgent()
    state = _make_state(issue="Add logout endpoint")
    result = agent.run(state)
    assert "plan" in result
    assert "Add logout endpoint" in result["plan"]
    assert result.get("workflow_node") == "planning"


@patch("agent_graph.agents.planner.OpenHandsClient")
@patch("agent_graph.agents.planner.clone_planner_workspace")
def test_planner_multi_repo_analysis(mock_clone, mock_client_cls):
    mock_clone.return_value = (
        "/tmp/planner_workspace_1",
        {"https://bitbucket.org/team/admin-ui.git": (
            "/tmp/planner_workspace_1/admin-ui",
            "baseline1",
        )},
    )
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.check_runtime_ready.return_value = None
    mock_client.run_cross_repo_planning.return_value = "## Cross-repo findings\nShared API"
    mock_client.run_planning_task.return_value = PlanningResult(
        success=True,
        summary="## Findings\nRoot cause in AdminComponent",
        planner_clone_path="/tmp/planner_workspace_1/admin-ui",
        repo_baseline_sha="baseline1",
    )

    agent = PlannerAgent()
    with patch.object(agent, "_analyse_repo", return_value="=== Repository Context ==="):
        with patch.object(agent, "_collect_grep_hints", return_value=""):
            state = _make_state(
                issue="Admin app shows wrong status",
                target_repos=[
                    {"target_repo_path": "https://bitbucket.org/team/admin-ui.git"},
                ],
            )
            result = agent.run(state)

    mock_clone.assert_called_once()
    mock_client.run_cross_repo_planning.assert_called_once()
    assert "admin-ui.git" in result["plan"]
    assert "## Findings" in result["plan"]
    repos = result["target_repos"]
    assert len(repos) == 1
    assert repos[0]["planner_clone_path"] == "/tmp/planner_workspace_1/admin-ui"
    assert repos[0]["repo_baseline_sha"] == "baseline1"
    assert "Root cause" in repos[0]["repo_summary"]
    mock_client.run_planning_task.assert_called_once()


@patch("agent_graph.agents.planner.OpenHandsClient")
@patch("agent_graph.agents.planner.clone_planner_workspace")
def test_planner_passes_input_prompt_to_openhands(mock_clone, mock_client_cls):
    mock_clone.return_value = (
        "/tmp/planner_workspace_1",
        {"https://bitbucket.org/team/admin-ui.git": (
            "/tmp/planner_workspace_1/admin-ui",
            "baseline1",
        )},
    )
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.check_runtime_ready.return_value = None
    mock_client.run_cross_repo_planning.return_value = ""
    mock_client.run_planning_task.return_value = PlanningResult(
        success=True,
        summary="## Findings\nDone",
        planner_clone_path="/tmp/planner_workspace_1/admin-ui",
        repo_baseline_sha="baseline1",
    )

    agent = PlannerAgent()
    with patch.object(agent, "_analyse_repo", return_value="=== Repository Context ==="):
        with patch.object(agent, "_collect_grep_hints", return_value=""):
            state = _make_state(
                issue="MINSKY-1: UI email flag",
                input_prompt="Also fix backend email verified handling",
                target_repos=[
                    {"target_repo_path": "https://bitbucket.org/team/admin-ui.git"},
                ],
            )
            result = agent.run(state)

    call_kwargs = mock_client.run_planning_task.call_args.kwargs
    assert call_kwargs["input_prompt"] == "Also fix backend email verified handling"
    assert "## Developer instructions" in result["plan"]
    assert "Also fix backend email verified handling" in result["plan"]


@patch("agent_graph.agents.planner.OpenHandsClient")
@patch("agent_graph.agents.planner.clone_planner_workspace")
def test_planner_continues_on_clone_failure(mock_clone, mock_client_cls):
    from agent_graph.exceptions import ExecutorError

    mock_clone.side_effect = ExecutorError("auth failed")
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.check_runtime_ready.return_value = None

    agent = PlannerAgent()
    state = _make_state(
        target_repos=[{"target_repo_path": "https://bitbucket.org/team/admin-ui.git"}],
    )
    result = agent.run(state)

    mock_client.run_planning_task.assert_not_called()
    assert "Clone failed" in result["target_repos"][0]["repo_summary"]


def test_analyse_repo_uses_compact_summary_for_large_repos(tmp_path):
    src = tmp_path / "src" / "main" / "java" / "com" / "example"
    src.mkdir(parents=True)
    for i in range(50):
        (src / f"Service{i}.java").write_text(
            f"package com.example;\npublic class Service{i} {{}}\n",
            encoding="utf-8",
        )

    agent = PlannerAgent()
    ctx = agent._analyse_repo(str(tmp_path))

    assert "File tree summary" in ctx
    assert len(ctx) < 3000
    assert "Service0.java" not in ctx
