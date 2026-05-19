"""Tests for GitHubFetcher."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from agent_graph.agents.github_fetcher import GitHubFetcher, GitHubIssue

FIXED_RESPONSE = [
    {
        "number": 42,
        "title": "Add authentication endpoint",
        "state": "open",
        "body": "Implement OAuth2 login flow",
        "html_url": "https://github.com/test/repo/issues/42",
        "assignee": {"login": "john"},
        "created": "2025-01-01T00:00:00Z",
    },
    {
        "number": 43,
        "title": "Fix database migration",
        "state": "open",
        "body": "Migration fails on production",
        "html_url": "https://github.com/test/repo/issues/43",
        "assignee": None,
        "created": "2025-01-02T00:00:00Z",
    },
    {
        "number": 44,
        "title": "Update README",
        "state": "open",
        "body": None,
        "html_url": "https://github.com/test/repo/issues/44",
        "assignee": {"login": "jane"},
        "created": "2025-01-03T00:00:00Z",
    },
]


class TestParseGithubUrl:
    @pytest.mark.parametrize("url,expected", [
        ("https://github.com/test-org/test-repo/issues/42", ("test-org", "test-repo", 42)),
        ("https://github.com/test-org/test-repo/pull/10", ("test-org", "test-repo", 10)),
        ("github:test-org/test-repo/issues/5", ("test-org", "test-repo", 5)),
        ("https://github.com/test-org/test-repo", ("test-org", "test-repo", None)),
        ("github:test-org/test-repo", ("test-org", "test-repo", None)),
    ])
    def test_parse_valid_urls(self, url, expected):
        fetcher = GitHubFetcher(token="fake")
        result = fetcher.parse_github_url(url)
        assert result == expected

    def test_parse_invalid_url_raises(self):
        fetcher = GitHubFetcher(token="fake")
        with pytest.raises(ValueError, match="Cannot parse GitHub URL"):
            fetcher.parse_github_url("not-a-valid-url")

    def test_parse_github_url_with_trailing_slash(self):
        fetcher = GitHubFetcher(token="fake")
        result = fetcher.parse_github_url("https://github.com/test-org/test-repo/")
        assert result == ("test-org", "test-repo", None)


class TestFetchIssues:
    def test_fetch_issues_success(self):
        fetcher = GitHubFetcher(token="fake-token")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = FIXED_RESPONSE
        with patch.object(requests, "get", return_value=mock_response) as mock_get:
            issues = fetcher.fetch_issues("test", "repo")
            assert len(issues) == 3
            assert isinstance(issues[0], GitHubIssue)
            assert issues[0].number == 42
            assert issues[0].assignee == "john"
            assert issues[1].assignee is None
            assert issues[2].body == ""
            mock_get.assert_called_once()

    def test_fetch_issues_api_error(self):
        fetcher = GitHubFetcher(token="fake-token")
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = '{"message": "Not Found"}'
        with patch.object(requests, "get", return_value=mock_response), pytest.raises(RuntimeError, match="GitHub API error 404"):
                fetcher.fetch_issues("test", "repo")

    def test_fetch_issues_default_state(self):
        fetcher = GitHubFetcher(token="fake-token")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        with patch.object(requests, "get", return_value=mock_response) as mock_get:
            issues = fetcher.fetch_issues("test", "repo")
            assert issues == []
            call_kwargs = mock_get.call_args[1]
            assert call_kwargs["params"]["state"] == "open"

    def test_fetch_issues_respects_per_page_limit(self):
        fetcher = GitHubFetcher(token="fake-token")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        with patch.object(requests, "get", return_value=mock_response) as mock_get:
            fetcher.fetch_issues("test", "repo", per_page=200)
            call_kwargs = mock_get.call_args[1]
            assert call_kwargs["params"]["per_page"] == 100

    def test_fetch_issues_with_token(self):
        fetcher = GitHubFetcher(token="my-secret-token")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        with patch.object(requests, "get", return_value=mock_response) as mock_get:
            fetcher.fetch_issues("test", "repo")
            headers = mock_get.call_args[1]["headers"]
            assert headers["Authorization"] == "Bearer my-secret-token"

    def test_fetch_issues_without_token(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        fetcher = GitHubFetcher(token=None)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        with patch.object(requests, "get", return_value=mock_response) as mock_get:
            fetcher.fetch_issues("test", "repo")
            headers = mock_get.call_args[1]["headers"]
            assert "Authorization" not in headers


class _InputStarter:
    """Simple input mock that returns values from a list in sequence."""

    def __init__(self, values: list[str]):
        self.values = values
        self.i = 0

    def __call__(self, *_args) -> str:
        val = self.values[self.i]
        self.i += 1
        return val


class TestPickIssue:
    def test_pick_issue_valid_choice(self, monkeypatch):
        fetcher = GitHubFetcher()
        issues = [
            GitHubIssue(1, "First issue", "open", "alice", "body1", "https://gh/issues/1"),
            GitHubIssue(2, "Second issue", "open", None, "body2", "https://gh/issues/2"),
        ]
        monkeypatch.setattr("builtins.input", _InputStarter(["1"]))
        result = fetcher.pick_issue(issues)
        assert result.number == 1

    def test_pick_issue_invalid_then_valid(self, monkeypatch):
        fetcher = GitHubFetcher()
        issues = [
            GitHubIssue(1, "First", "open", None, "", ""),
            GitHubIssue(2, "Second", "open", None, "", ""),
        ]
        monkeypatch.setattr("builtins.input", _InputStarter(["99", "2"]))
        result = fetcher.pick_issue(issues)
        assert result.number == 2

    def test_pick_issue_empty_list_raises(self):
        fetcher = GitHubFetcher()
        with pytest.raises(RuntimeError, match="No issues found"):
            fetcher.pick_issue([])

    def test_pick_issue_keyboard_interrupt(self, monkeypatch):
        fetcher = GitHubFetcher()
        issues = [GitHubIssue(1, "First", "open", None, "", "")]
        monkeypatch.setattr("builtins.input", lambda _: "q")
        with pytest.raises(KeyboardInterrupt):
            fetcher.pick_issue(issues)
