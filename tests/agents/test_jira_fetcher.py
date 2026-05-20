"""Tests for JiraFetcher."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from agent_graph.agents.jira_fetcher import JiraFetcher, JiraIssue

FIXED_RESPONSE = {
    "issues": [
        {
            "key": "PROJ-42",
            "fields": {
                "summary": "Add authentication endpoint",
                "status": {"name": "To Do"},
                "assignee": {"displayName": "john"},
                "priority": {"name": "High"},
                "description": "Implement OAuth2 login flow",
                "project": {"key": "PROJ"},
            },
        },
        {
            "key": "PROJ-43",
            "fields": {
                "summary": "Fix database migration",
                "status": {"name": "In Progress"},
                "assignee": None,
                "priority": None,
                "description": "Migration fails on production",
                "project": {"key": "PROJ"},
            },
        },
        {
            "key": "PROJ-44",
            "fields": {
                "summary": "Update README",
                "status": {"name": "Open"},
                "assignee": {"displayName": "jane"},
                "priority": {"name": "Low"},
                "description": None,
                "project": {"key": "PROJ"},
            },
        },
    ],
}


class TestParseJiraUrl:
    @pytest.mark.parametrize("url,expected_project,expected_key", [
        ("https://acme.atlassian.net/browse/PROJ-42", "PROJ", "PROJ-42"),
        ("https://my-org.atlassian.net/browse/MY-PROJECT-123", "MY-PROJECT", "MY-PROJECT-123"),
        ("PROJ-42", "PROJ", "PROJ-42"),
        ("MY-PROJECT-123", "MY-PROJECT", "MY-PROJECT-123"),
    ])
    def test_parse_valid_url(self, url, expected_project, expected_key):
        project_key, _ = JiraFetcher.parse_jira_url(url)
        assert project_key == expected_key

    def test_parse_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Cannot parse Jira URL"):
            JiraFetcher.parse_jira_url("not-a-valid-url")


class TestFetchIssues:
    def test_fetch_issues_success(self):
        fetcher = JiraFetcher(token="fake-token", site="test-site")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = FIXED_RESPONSE
        with patch.object(requests, "get", return_value=mock_response) as mock_get:
            issues = fetcher.fetch_issues('project = "PROJ"')
            assert len(issues) == 3
            assert isinstance(issues[0], JiraIssue)
            assert issues[0].key == "PROJ-42"
            assert issues[0].title == "Add authentication endpoint"
            assert issues[0].assignee == "john"
            assert issues[0].priority == "High"

    def test_fetch_issues_api_error(self):
        fetcher = JiraFetcher(token="fake-token", site="test-site")
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = '{"message": "Not Found"}'
        with patch.object(requests, "get", return_value=mock_response), pytest.raises(RuntimeError, match="Jira API error 404"):
            fetcher.fetch_issues('project = "PROJ"')

    def test_fetch_issues_with_token(self):
        fetcher = JiraFetcher(token="my-secret", site="test-site")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"issues": []}
        with patch.object(requests, "get", return_value=mock_response) as mock_get:
            fetcher.fetch_issues('project = "PROJ"')
            headers = mock_get.call_args[1]["headers"]
            assert headers["Authorization"] == "Bearer my-secret"

    def test_fetch_issues_default_jql(self):
        fetcher = JiraFetcher(token="fake-token", site="test-site")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"issues": []}
        with patch.object(requests, "get", return_value=mock_response) as mock_get:
            fetcher.fetch_issues('project = "DEFAULT"')
            assert mock_get.call_args[1]["params"]["jql"] == 'project = "DEFAULT"'

    def test_fetch_issues_respects_max_results(self):
        fetcher = JiraFetcher(token="fake-token", site="test-site")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"issues": []}
        with patch.object(requests, "get", return_value=mock_response) as mock_get:
            fetcher.fetch_issues('project = "PROJ"', max_results=200)
            assert mock_get.call_args[1]["params"]["maxResults"] == 100

    def test_fetch_issues_descriptions_none(self):
        fetcher = JiraFetcher(token="fake-token", site="test-site")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "issues": [{
                "key": "PROJ-1",
                "fields": {
                    "summary": "Test",
                    "status": {"name": "Open"},
                    "assignee": None,
                    "priority": None,
                    "description": None,
                    "project": {"key": "PROJ"},
                },
            }],
        }
        with patch.object(requests, "get", return_value=mock_response):
            issues = fetcher.fetch_issues('project = "PROJ"')
            assert len(issues) == 1
            assert issues[0].body == ""


class TestFetchIssue:
    def test_fetch_single_issue(self):
        fetcher = JiraFetcher(token="fake-token", site="test-site")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "key": "PROJ-42",
            "fields": {
                "summary": "Single issue",
                "status": {"name": "In Progress"},
                "assignee": {"displayName": "alice"},
                "priority": {"name": "Medium"},
                "description": "Body text here",
                "project": {"key": "PROJ"},
            },
        }
        with patch.object(requests, "get", return_value=mock_response) as mock_get:
            issue = fetcher.fetch_issue("PROJ-42")
            assert issue.key == "PROJ-42"
            assert issue.title == "Single issue"
            assert issue.assignee == "alice"
            assert issue.url == "https://test-site.atlassian.net/browse/PROJ-42"
            assert mock_get.call_args[0][1] == "https://test-site.atlassian.net/rest/api/latest/issue/PROJ-42"


class TestGetProjectKeys:
    def test_get_project_keys(self):
        fetcher = JiraFetcher(token="fake-token", site="test-site")
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "values": [
                {"key": "PROJ", "name": "Project"},
                {"key": "OTHER", "name": "Other"},
            ]
        }
        with patch.object(requests, "get", return_value=mock_response) as mock_get:
            keys = fetcher.get_project_keys()
            assert keys == ["PROJ", "OTHER"]


class TestSuggestJql:
    def test_suggest_jql_default(self):
        fetcher = JiraFetcher(token="fake", site="test")
        jql = fetcher.suggest_jql("PROJ")
        assert 'project = "PROJ"' in jql
        assert "To Do" in jql
        assert "In Progress" in jql


class TestPickIssue:
    def test_pick_issue_valid_choice(self, monkeypatch):
        fetcher = JiraFetcher(token="fake", site="test")
        issues = [
            JiraIssue(key="PROJ-1", title="First", state="open", assignee="alice", body="body1", url="https://jira/browse/PROJ-1"),
            JiraIssue(key="PROJ-2", title="Second", state="open", assignee=None, body="body2", url="https://jira/browse/PROJ-2"),
        ]
        monkeypatch.setattr("builtins.input", lambda _: "1")
        result = fetcher.pick_issue(issues)
        assert result.key == "PROJ-1"

    def test_pick_issue_keyboard_interrupt(self, monkeypatch):
        fetcher = JiraFetcher(token="fake", site="test")
        issues = [JiraIssue(key="PROJ-1", title="First", state="open", assignee=None, body="", url="")]
        monkeypatch.setattr("builtins.input", lambda _: "q")
        with pytest.raises(KeyboardInterrupt):
            fetcher.pick_issue(issues)

    def test_pick_issue_empty_list_raises(self):
        fetcher = JiraFetcher(token="fake", site="test")
        with pytest.raises(RuntimeError, match="No issues found"):
            fetcher.pick_issue([])
