"""Tests for PrCreatorAgent."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from agent_graph.agents.pr_creator import PrCreatorAgent
from agent_graph.state import TaskState


def _state(**kwargs) -> TaskState:
    base: TaskState = {
        "issue": "Fix login bug",
        "plan": "1. Fix auth",
        "implementation_result": "done",
        "verification_result": "passed",
        "target_repo_path": "https://github.com/acme/app.git",
        "work_repo_path": "/tmp/work",
        "diff_patch": "diff content",
        "github_issue_url": "https://github.com/acme/app/issues/42",
        "pr_url": "",
        "pr_error": "",
    }
    base.update(kwargs)
    return base


def test_skip_when_no_work_repo():
    agent = PrCreatorAgent()
    result = agent.run(_state(work_repo_path=""))
    assert result["pr_url"] == ""
    assert result["pr_error"] == ""
    assert "No work repository" in result["pr_skip_reason"]


def test_skip_when_no_changes():
    agent = PrCreatorAgent()
    with patch(
        "agent_graph.agents.pr_creator.has_changes_since", return_value=False
    ):
        result = agent.run(_state(repo_baseline_sha="abc123"))
    assert result["pr_url"] == ""
    assert "No changes detected" in result["pr_skip_reason"]


def test_missing_token_raises():
    agent = PrCreatorAgent()
    with patch(
        "agent_graph.agents.pr_creator.has_changes_since", return_value=True
    ):
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
                agent.run(_state())


def test_happy_path_uncommitted():
    agent = PrCreatorAgent()
    with patch(
        "agent_graph.agents.pr_creator.has_changes_since", return_value=True
    ):
        with patch(
            "agent_graph.agents.pr_creator.has_uncommitted_changes", return_value=True
        ):
            with patch.object(PrCreatorAgent, "_git"):
                with patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_test"}):
                    with patch(
                        "agent_graph.agents.pr_creator.GitHubFetcher"
                    ) as mock_fetcher_cls:
                        mock_fetcher_cls.parse_repo_remote.return_value = (
                            "acme",
                            "app",
                        )
                        mock_fetcher = MagicMock()
                        mock_fetcher_cls.return_value = mock_fetcher
                        mock_fetcher.get_default_branch.return_value = "main"
                        mock_fetcher.create_pull_request.return_value = (
                            "https://github.com/acme/app/pull/99"
                        )
                        result = agent.run(_state())

    assert result["pr_url"] == "https://github.com/acme/app/pull/99"
    assert result["pr_error"] == ""


def test_happy_path_agent_already_committed():
    agent = PrCreatorAgent()
    with patch(
        "agent_graph.agents.pr_creator.has_changes_since", return_value=True
    ):
        with patch(
            "agent_graph.agents.pr_creator.has_uncommitted_changes", return_value=False
        ):
            with patch(
                "agent_graph.agents.pr_creator.commit_count_since", return_value=2
            ):
                with patch.object(PrCreatorAgent, "_git") as mock_git:
                    with patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_test"}):
                        with patch(
                            "agent_graph.agents.pr_creator.GitHubFetcher"
                        ) as mock_fetcher_cls:
                            mock_fetcher_cls.parse_repo_remote.return_value = (
                                "acme",
                                "app",
                            )
                            mock_fetcher = MagicMock()
                            mock_fetcher_cls.return_value = mock_fetcher
                            mock_fetcher.get_default_branch.return_value = "main"
                            mock_fetcher.create_pull_request.return_value = (
                                "https://github.com/acme/app/pull/100"
                            )
                            result = agent.run(_state(repo_baseline_sha="deadbeef"))

    assert result["pr_url"] == "https://github.com/acme/app/pull/100"
    commit_calls = [c for c in mock_git.call_args_list if c[0][1] == "commit"]
    assert len(commit_calls) == 0


def test_push_failure_returns_error():
    agent = PrCreatorAgent()
    with patch(
        "agent_graph.agents.pr_creator.has_changes_since", return_value=True
    ):
        with patch(
            "agent_graph.agents.pr_creator.has_uncommitted_changes", return_value=True
        ):
            with patch.object(
                PrCreatorAgent,
                "_git",
                side_effect=subprocess.CalledProcessError(
                    1, "git", stderr=b"permission denied"
                ),
            ):
                with patch(
                    "agent_graph.agents.pr_creator.GitHubFetcher.parse_repo_remote",
                    return_value=("acme", "app"),
                ):
                    with patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_test"}):
                        result = agent.run(_state())

    assert result["pr_url"] == ""
    assert "permission denied" in result["pr_error"]
