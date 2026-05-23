"""Tests for BitbucketFetcher."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from agent_graph.agents.bitbucket_fetcher import BitbucketFetcher, BitbucketIssue

FIXED_ISSUES_RESPONSE = {
    "values": [
        {
            "id": 1,
            "title": "Add login page",
            "state": {"name": "Open"},
            "reporter": {"username": "dev1"},
            "assignee": {"username": "dev2"},
            "content": {"raw": "Implement login screen"},
            "links": {
                "html": {"href": "https://bitbucket.org/acme/myapp/issues/1"},
                "jira": {"issues": []},
            },
        },
        {
            "id": 2,
            "title": "Fix navigation bug",
            "state": {"name": "Open"},
            "reporter": {"username": "dev3"},
            "assignee": None,
            "user": {"display_name": "dev3"},
            "content": {"raw": "Nav fails on mobile"},
            "links": {
                "html": {"href": "https://bitbucket.org/acme/myapp/issues/2"},
                "jira": {"issues": []},
            },
        },
    ],
}


class TestParseRepoRemote:
    def test_from_https_clone_url(self):
        owner, repo = BitbucketFetcher.parse_repo_remote(
            "https://bitbucket.org/acme/myapp.git",
        )
        assert owner == "acme"
        assert repo == "myapp"

    def test_from_git_ssh_url(self):
        owner, repo = BitbucketFetcher.parse_repo_remote(
            "git@bitbucket.org:acme/myapp.git",
        )
        assert owner == "acme"
        assert repo == "myapp"

    def test_from_issue_url(self):
        owner, repo = BitbucketFetcher.parse_repo_remote(
            "", "https://bitbucket.org/acme/myapp/issues/5",
        )
        assert owner == "acme"
        assert repo == "myapp"

    def test_from_pull_request_url(self):
        owner, repo = BitbucketFetcher.parse_repo_remote(
            "", "https://bitbucket.org/acme/myapp/pull-requests/10",
        )
        assert owner == "acme"
        assert repo == "myapp"

    def test_from_unparseable_path(self):
        with pytest.raises(ValueError, match="Cannot resolve Bitbucket"):
            BitbucketFetcher.parse_repo_remote("/local/path")


class TestParseIssueUrl:
    @pytest.mark.parametrize("url,expected", [
        ("https://bitbucket.org/acme/myapp/issues/42", ("acme", "myapp")),
        ("https://bitbucket.org/acme/myapp/pull-requests/10", ("acme", "myapp")),
        ("https://bitbucket.org/acme/myapp/src/main", ("acme", "myapp")),
    ])
    def test_parse_valid_urls(self, url, expected):
        result = BitbucketFetcher.parse_issue_url(url)
        assert result == expected

    def test_parse_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Cannot parse Bitbucket URL"):
            BitbucketFetcher.parse_issue_url("not-a-valid-url")


class TestGetDefaultBranch:
    def test_get_default_branch_from_repo_mainbranch(self):
        fetcher = BitbucketFetcher(token="fake")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "mainbranch": {"name": "master", "type": "branch"},
        }
        with patch.object(requests, "get", return_value=mock_response) as mock_get:
            branch = fetcher.get_default_branch("acme", "myapp")
            assert branch == "master"
            assert mock_get.call_args[0][0] == (
                "https://api.bitbucket.org/2.0/repositories/acme/myapp"
            )

    def test_get_default_branch_env_override(self, monkeypatch):
        monkeypatch.setenv("BITBUCKET_PR_BASE_BRANCH", "main")
        fetcher = BitbucketFetcher(token="fake")
        with patch.object(requests, "get") as mock_get:
            branch = fetcher.get_default_branch("acme", "myapp")
            assert branch == "main"
            mock_get.assert_not_called()

    def test_get_default_branch_master_fallback(self):
        fetcher = BitbucketFetcher(token="fake")
        repo_resp = MagicMock(status_code=200, json=MagicMock(return_value={}))
        master_resp = MagicMock(status_code=200)

        def fake_get(url, **kwargs):
            if url.endswith("/refs/branches/master"):
                return master_resp
            return repo_resp

        with patch.object(requests, "get", side_effect=fake_get):
            branch = fetcher.get_default_branch("acme", "myapp")
            assert branch == "master"

    def test_get_default_branch_unresolved_raises(self):
        fetcher = BitbucketFetcher(token="fake")
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = '{"message": "Not found"}'
        with patch.object(requests, "get", return_value=mock_response), pytest.raises(
            RuntimeError, match="Could not resolve default branch"
        ):
            fetcher.get_default_branch("acme", "myapp")


class TestFetchIssues:
    def test_fetch_issues_success(self):
        fetcher = BitbucketFetcher(token="fake-token")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = FIXED_ISSUES_RESPONSE
        with patch.object(requests, "get", return_value=mock_response) as mock_get:
            issues = fetcher.fetch_issues("acme", "myapp")
            assert len(issues) == 2
            assert isinstance(issues[0], BitbucketIssue)
            assert issues[0].id == 1
            assert issues[0].title == "Add login page"
            assert issues[0].assignee == "dev2"
            assert issues[0].body == "Implement login screen"
            mock_get.assert_called_once()

    def test_fetch_issues_api_error(self):
        fetcher = BitbucketFetcher(token="fake-token")
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = '{"message": "Not found"}'
        with patch.object(requests, "get", return_value=mock_response), pytest.raises(RuntimeError, match="Bitbucket API error 404"):
            fetcher.fetch_issues("acme", "myapp")

    def test_fetch_issues_empty(self):
        fetcher = BitbucketFetcher(token="fake-token")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"values": []}
        with patch.object(requests, "get", return_value=mock_response):
            issues = fetcher.fetch_issues("acme", "myapp")
            assert issues == []

    def test_fetch_issues_with_jira_links(self):
        fetcher = BitbucketFetcher(token="fake-token")
        response_with_jira = FIXED_ISSUES_RESPONSE.copy()
        response_with_jira["values"][0]["links"] = {
            "html": {"href": "https://old/issues/1"},
            "jira": {"issues": [{"key": "PROJ-50", "fields": {"summary": "Jira linked task", "priority": {"name": "High"}}}]},
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_with_jira
        with patch.object(requests, "get", return_value=mock_response):
            issues = fetcher.fetch_issues("acme", "myapp")
            assert issues[0].title == "Jira linked task"
            assert issues[0].priority == "High"

    def test_fetch_issues_bearer_when_explicit_token_only(self):
        fetcher = BitbucketFetcher(token="my-secret-token")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"values": []}
        with patch.object(requests, "get", return_value=mock_response) as mock_get:
            fetcher.fetch_issues("acme", "myapp")
            headers = mock_get.call_args[1]["headers"]
            assert headers["Authorization"] == "Bearer my-secret-token"
            assert "auth" not in mock_get.call_args[1]

    def test_fetch_issues_basic_auth_with_email(self, monkeypatch):
        monkeypatch.setenv("BITBUCKET_TOKEN", "bb-tok")
        monkeypatch.delenv("BB_USERNAME", raising=False)
        monkeypatch.delenv("ATLASSIAN_EMAIL", raising=False)
        monkeypatch.setenv("JIRA_EMAIL", "user@example.com")
        fetcher = BitbucketFetcher()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"values": []}
        with patch.object(requests, "get", return_value=mock_response) as mock_get:
            fetcher.fetch_issues("acme", "myapp")
            assert mock_get.call_args[1]["auth"] == ("user@example.com", "bb-tok")
            assert "Authorization" not in mock_get.call_args[1]["headers"]


class TestCreatePullRequest:
    def test_create_pull_request_success(self):
        fetcher = BitbucketFetcher(token="fake")
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "links": {"html": {"href": "https://bitbucket.org/acme/myapp/pull-requests/1"}}
        }
        with patch.object(requests, "post", return_value=mock_response) as mock_post:
            url = fetcher.create_pull_request("acme", "myapp", "Add feature", "feature", "main", "Body")
            assert url == "https://bitbucket.org/acme/myapp/pull-requests/1"
            assert mock_post.call_args[0][0] == (
                "https://api.bitbucket.org/2.0/repositories/acme/myapp/pullrequests"
            )

    def test_create_pull_request_api_error(self):
        fetcher = BitbucketFetcher(token="fake")
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"error": "duplicate"}'
        with patch.object(requests, "post", return_value=mock_response), pytest.raises(RuntimeError, match="Bitbucket API error 400"):
            fetcher.create_pull_request("acme", "myapp", "Title", "feat", "main", "Body")


class TestPickIssue:
    def test_pick_issue_valid_choice(self, monkeypatch):
        fetcher = BitbucketFetcher(token="fake")
        issues = [
            BitbucketIssue(id=1, title="First", state="open", assignee="alice", body="body1", url="https://bb/issues/1"),
            BitbucketIssue(id=2, title="Second", state="open", assignee=None, body="body2", url="https://bb/issues/2"),
        ]
        monkeypatch.setattr("builtins.input", lambda _: "1")
        result = fetcher.pick_issue(issues)
        assert result.id == 1

    def test_pick_issue_invalid_then_valid(self, monkeypatch):
        fetcher = BitbucketFetcher(token="fake")
        issues = [
            BitbucketIssue(id=1, title="First", state="open", assignee=None, body="", url=""),
            BitbucketIssue(id=2, title="Second", state="open", assignee=None, body="", url=""),
        ]
        monkeypatch.setattr("builtins.input", lambda _: "99")
        def mock_input(prompt):
            if not hasattr(mock_input, "called"):
                mock_input.called = True
                return "2"
            return "2"
        mock_input.called = False
        monkeypatch.setattr("builtins.input", mock_input)
        result = fetcher.pick_issue(issues)
        assert result.id == 2

    def test_pick_issue_empty_list_raises(self):
        fetcher = BitbucketFetcher(token="fake")
        with pytest.raises(RuntimeError, match="No issues found"):
            fetcher.pick_issue([])

    def test_pick_issue_keyboard_interrupt(self, monkeypatch):
        fetcher = BitbucketFetcher(token="fake")
        issues = [BitbucketIssue(id=1, title="First", state="open", assignee=None, body="", url="")]
        monkeypatch.setattr("builtins.input", lambda _: "q")
        with pytest.raises(KeyboardInterrupt):
            fetcher.pick_issue(issues)
