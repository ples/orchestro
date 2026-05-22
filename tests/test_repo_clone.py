"""Tests for repo_clone helpers."""

import os
import subprocess
from unittest.mock import patch

import pytest

from agent_graph.exceptions import ExecutorError
from agent_graph.repo_clone import (
    authenticated_clone_url,
    clone_repository,
    clone_timeout_seconds,
    reset_repo_to_baseline,
)


def test_authenticated_git_url_github():
    with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test"}, clear=False):
        url = authenticated_clone_url("https://github.com/org/repo.git")
    assert url == "https://x-access-token:ghp_test@github.com/org/repo.git"


def test_authenticated_git_url_bitbucket():
    from agent_graph.repo_clone import BITBUCKET_GIT_USERNAME

    env = {
        "BITBUCKET_TOKEN": "bb_tok",
        "JIRA_EMAIL": "user@example.com",
        "BB_USERNAME": "",
        "ATLASSIAN_EMAIL": "",
        "GITHUB_TOKEN": "",
    }
    with patch.dict(os.environ, env, clear=False):
        url = authenticated_clone_url("https://bitbucket.org/team/repo.git")
    assert BITBUCKET_GIT_USERNAME in url
    assert "bb_tok" in url
    assert url == (
        f"https://{BITBUCKET_GIT_USERNAME}:bb_tok@bitbucket.org/team/repo.git"
    )


def test_authenticated_git_url_skips_when_credentials_embedded():
    url = authenticated_clone_url("https://user:pass@bitbucket.org/team/repo.git")
    assert url == "https://user:pass@bitbucket.org/team/repo.git"


def test_clone_timeout_default():
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("GIT_CLONE_TIMEOUT", None)
        assert clone_timeout_seconds() == 120


def test_clone_timeout_from_env():
    with patch.dict(os.environ, {"GIT_CLONE_TIMEOUT": "60"}):
        assert clone_timeout_seconds() == 60


@patch("agent_graph.repo_clone.rev_parse", return_value="abc123")
@patch("agent_graph.repo_clone.subprocess.run")
@patch("agent_graph.repo_clone.subprocess.check_output", return_value="/tmp/agent_repo_xyz\n")
def test_clone_repository_remote(mock_mktemp, mock_run, mock_rev):
    mock_run.return_value = subprocess.CompletedProcess([], 0, "", "")

    with patch(
        "agent_graph.repo_clone.authenticated_clone_url",
        return_value="https://token@github.com/org/repo.git",
    ):
        path, sha = clone_repository("https://github.com/org/repo.git")

    assert path.endswith("/repo")
    assert sha == "abc123"
    clone_calls = [c for c in mock_run.call_args_list if c[0][0][:2] == ["git", "clone"]]
    assert len(clone_calls) == 1
    assert clone_calls[0][1]["timeout"] == 120


@patch("agent_graph.repo_clone.subprocess.run")
@patch("agent_graph.repo_clone.subprocess.check_output", return_value="/tmp/agent_repo_xyz\n")
def test_clone_repository_timeout(mock_mktemp, mock_run):
    mock_run.side_effect = subprocess.TimeoutExpired(cmd=["git", "clone"], timeout=120)

    with patch(
        "agent_graph.repo_clone.authenticated_clone_url",
        return_value="https://github.com/org/repo.git",
    ):
        with pytest.raises(ExecutorError, match="timed out"):
            clone_repository("https://github.com/org/repo.git", timeout=120)


@patch("agent_graph.repo_clone.subprocess.run")
def test_reset_repo_to_baseline(mock_run):
    reset_repo_to_baseline("/tmp/repo", "deadbeef")
    assert mock_run.call_count == 2
