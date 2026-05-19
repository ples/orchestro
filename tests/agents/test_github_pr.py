"""Tests for GitHubFetcher PR helpers."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from agent_graph.agents.github_fetcher import GitHubFetcher


class TestParseRepoRemote:
    def test_from_https_clone_url(self):
        owner, repo = GitHubFetcher.parse_repo_remote(
            "https://github.com/acme/my-app.git"
        )
        assert owner == "acme"
        assert repo == "my-app"

    def test_from_git_ssh_url(self):
        owner, repo = GitHubFetcher.parse_repo_remote(
            "git@github.com:acme/my-app.git"
        )
        assert owner == "acme"
        assert repo == "my-app"

    def test_from_issue_url(self):
        owner, repo = GitHubFetcher.parse_repo_remote(
            github_issue_url="https://github.com/acme/my-app/issues/1"
        )
        assert owner == "acme"
        assert repo == "my-app"

    def test_unparseable_raises(self):
        with pytest.raises(ValueError):
            GitHubFetcher.parse_repo_remote("/local/path")


class TestCreatePullRequest:
    def test_create_pull_request_success(self):
        fetcher = GitHubFetcher(token="fake")
        with patch.object(requests, "post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=201,
                json=lambda: {"html_url": "https://github.com/o/r/pull/1"},
            )
            url = fetcher.create_pull_request(
                "o", "r", "title", "feature", "main", "body"
            )
        assert url == "https://github.com/o/r/pull/1"
