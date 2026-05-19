"""Tests for VerifierAgent."""

from agent_graph.agents.verifier import VerifierAgent
from agent_graph.state import TaskState


def test_verifier_returns_result():
    agent = VerifierAgent()
    state: TaskState = {
        "issue": "Add feature",
        "plan": "plan",
        "implementation_result": "implemented",
        "verification_result": "",
        "pr_url": "",
        "pr_error": "",
        "target_repo_path": "",
        "diff_patch": "",
        "github_issue_url": "",
    }
    result = agent.run(state)
    assert "verification_result" in result
    assert result["verification_result"] == "All tests passed"
