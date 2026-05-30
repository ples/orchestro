"""Tests for VerifierAgent."""

from unittest.mock import patch

from agent_graph.agents.verifier import VerifierAgent
from agent_graph.pr_skip import NO_CHANGES_SKIP, REQUIRED_CHANGES_MISSING
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


@patch("agent_graph.agents.verifier.run_checks")
@patch("agent_graph.agents.verifier.has_changes_since", return_value=True)
def test_verifier_passes_required_repo_with_changes(_mock_diff, mock_checks):
    from agent_graph.repo_verification import RepoCheckResult

    mock_checks.return_value = RepoCheckResult(passed=True, command="npm test", details="ok")
    agent = VerifierAgent()
    state: TaskState = {
        "target_repos": [
            {
                "target_repo_path": "https://bitbucket.org/dmetrics/admin-ui.git",
                "work_repo_path": "/tmp/admin-ui",
                "repo_baseline_sha": "abc",
                "requires_changes": True,
            }
        ],
    }
    result = agent.run(state)
    assert "passed" in result["verification_result"]
    assert not result.get("pr_error")


def test_verifier_fails_required_repo_with_pr_error():
    agent = VerifierAgent()
    state: TaskState = {
        "target_repos": [
            {
                "target_repo_path": "https://bitbucket.org/dmetrics/admin-ui.git",
                "requires_changes": True,
                "pr_error": "Required changes missing in admin-ui",
                "execution_diagnostics": "repo: admin-ui",
            }
        ],
    }
    result = agent.run(state)
    assert "failed" in result["verification_result"]
    assert "Required changes missing" in result.get("pr_error", "")


def test_verifier_skips_optional_no_changes():
    agent = VerifierAgent()
    state: TaskState = {
        "target_repos": [
            {
                "target_repo_path": "https://bitbucket.org/dmetrics/identity-hub-api.git",
                "requires_changes": False,
                "pr_skip_reason": NO_CHANGES_SKIP,
            }
        ],
    }
    result = agent.run(state)
    assert "skipped" in result["verification_result"]
    assert not result.get("pr_error")


def test_verifier_fails_required_changes_missing_skip():
    agent = VerifierAgent()
    state: TaskState = {
        "target_repos": [
            {
                "target_repo_path": "https://bitbucket.org/dmetrics/admin-ui.git",
                "requires_changes": True,
                "pr_skip_reason": REQUIRED_CHANGES_MISSING,
                "execution_diagnostics": "expected_targets: GroupDetails.tsx",
            }
        ],
    }
    result = agent.run(state)
    assert "failed" in result["verification_result"]
    assert result.get("pr_error")
