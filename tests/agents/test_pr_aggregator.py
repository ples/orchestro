"""Tests for PrAggregatorAgent multi-repo behavior."""

import subprocess
from unittest.mock import patch

from agent_graph.agents.pr_aggregator import PrAggregatorAgent
from agent_graph.pr_skip import NO_CHANGES_SKIP, REQUIRED_CHANGES_MISSING
from agent_graph.state import TaskState


class TestPrAggregatorResolveOwnerRepo:
    def test_resolves_owner_repo_for_specific_record_not_first(self):
        agent = PrAggregatorAgent()
        state: TaskState = {
            "jira_issue_url": "https://dmetrics.atlassian.net/browse/MINSKY-14576",
            "target_repos": [
                {"target_repo_path": "https://bitbucket.org/dmetrics/identity-hub-api.git"},
                {"target_repo_path": "https://bitbucket.org/dmetrics/account-ui.git"},
            ],
        }
        account_ui = state["target_repos"][1]
        owner, repo = agent._resolve_owner_repo(state, repo=account_ui, platform_github=False)
        assert owner == "dmetrics"
        assert repo == "account-ui"

        identity_hub = state["target_repos"][0]
        owner2, repo2 = agent._resolve_owner_repo(state, repo=identity_hub, platform_github=False)
        assert owner2 == "dmetrics"
        assert repo2 == "identity-hub-api"


class TestPrAggregatorNoChanges:
    def test_executor_no_changes_message_treated_as_skip(self):
        agent = PrAggregatorAgent()
        state: TaskState = {
            "target_repos": [
                {
                    "target_repo_path": "https://bitbucket.org/dmetrics/identity-hub-api.git",
                    "work_repo_path": "/tmp/identity-hub-api",
                    "repo_baseline_sha": "abc",
                    "pr_error": (
                        "No file changes in identity-hub-api — "
                        "OpenHands finished without editing the repo"
                    ),
                },
                {
                    "target_repo_path": "https://bitbucket.org/dmetrics/account-ui.git",
                    "work_repo_path": "/tmp/account-ui",
                    "repo_baseline_sha": "def",
                    "pr_skip_reason": "no_changes",
                },
            ],
        }
        result = agent.run(state)
        assert result.get("pr_error", "") == ""
        assert "identity-hub-api.git" in result.get("pr_skip_reason", "")
        assert "account-ui.git" in result.get("pr_skip_reason", "")
        assert "No changes required" in result.get("pr_skip_reason", "")

    def test_required_changes_missing_treated_as_error(self):
        agent = PrAggregatorAgent()
        state: TaskState = {
            "target_repos": [
                {
                    "target_repo_path": "https://bitbucket.org/dmetrics/admin-ui.git",
                    "work_repo_path": "/tmp/admin-ui",
                    "repo_baseline_sha": "abc",
                    "pr_skip_reason": REQUIRED_CHANGES_MISSING,
                    "pr_error": "Required changes missing in admin-ui",
                },
            ],
        }
        result = agent.run(state)
        assert "admin-ui.git" in result.get("pr_error", "")
        assert REQUIRED_CHANGES_MISSING not in result.get("pr_skip_reason", "")

    def test_optional_no_changes_still_skipped(self):
        agent = PrAggregatorAgent()
        state: TaskState = {
            "target_repos": [
                {
                    "target_repo_path": "https://bitbucket.org/dmetrics/identity-hub-api.git",
                    "work_repo_path": "/tmp/identity-hub-api",
                    "repo_baseline_sha": "abc",
                    "requires_changes": False,
                    "pr_skip_reason": NO_CHANGES_SKIP,
                },
            ],
        }
        result = agent.run(state)
        assert result.get("pr_error", "") == ""
        assert "identity-hub-api.git" in result.get("pr_skip_reason", "")


class TestPrAggregatorDeployTag:
    def test_prepare_deploy_env_tag_skipped_when_empty(self):
        assert PrAggregatorAgent._prepare_deploy_env_tag({}, "/tmp", "branch") == {}

    @patch("agent_graph.agents.pr_aggregator.prepare_deploy_env_tag")
    def test_prepare_deploy_env_tag_calls_helper(self, mock_prepare):
        mock_prepare.return_value = ("env.dev.branch.feature/foo", "")
        result = PrAggregatorAgent._prepare_deploy_env_tag(
            {"deploy_env": "dev"},
            "/tmp/repo",
            "feature/foo",
        )
        mock_prepare.assert_called_once_with("/tmp/repo", "feature/foo", "dev")
        assert result["deploy_tag_name"] == "env.dev.branch.feature/foo"
        assert result["deploy_tag_error"] == ""

    @patch("agent_graph.agents.pr_aggregator.prepare_deploy_env_tag")
    def test_prepare_deploy_env_tag_returns_error(self, mock_prepare):
        mock_prepare.return_value = ("", "Git tag failed: bad state")
        result = PrAggregatorAgent._prepare_deploy_env_tag(
            {"deploy_env": "stage"},
            "/tmp/repo",
            "hotfix/bar",
        )
        assert result["deploy_tag_name"] == ""
        assert "Git tag failed" in result["deploy_tag_error"]

    def test_push_branch_includes_deploy_tag_in_push_refs(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@e.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "f").write_text("x")
        subprocess.run(["git", "add", "f"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "c"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "tag", "-a", "env.dev.branch.main", "-m", "t"],
            cwd=repo,
            check=True,
            capture_output=True,
        )

        agent = PrAggregatorAgent()
        calls: list[list[str]] = []

        def capture_git(repo_path, *args):
            calls.append(list(args))

        agent._git = capture_git  # type: ignore[method-assign]
        mode = agent._push_branch(
            str(repo),
            "main",
            deploy_tag_name="env.dev.branch.main",
        )
        assert mode == "normal"
        assert calls
        assert calls[0][0] == "push"
        assert "env.dev.branch.main" in calls[0]
        assert "main" in calls[0]

    def test_push_branch_blocks_hard_force_when_disabled(self):
        agent = PrAggregatorAgent()
        call_counter = {"push": 0}

        def fake_git(_repo_path, *args):
            if args[0] == "push":
                call_counter["push"] += 1
                raise subprocess.CalledProcessError(
                    1, ["git", "push"], None, b"rejected"
                )
            if args[0] == "rebase":
                raise subprocess.CalledProcessError(1, ["git", "rebase"], None, b"fail")

        agent._git = fake_git  # type: ignore[method-assign]
        with patch("agent_graph.agents.pr_aggregator.refresh_deploy_env_tag_at_head", return_value=""):
            with patch("agent_graph.agents.pr_aggregator.os.getenv", return_value="0"):
                try:
                    agent._push_branch("/tmp/repo", "main")
                    assert False, "expected CalledProcessError"
                except subprocess.CalledProcessError as exc:
                    assert b"Force push blocked" in exc.stderr
                    assert call_counter["push"] >= 2
