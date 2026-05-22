"""Tests for McpJiraFetcher parsing."""

from agent_graph.agents.jira_mcp_fetcher import McpJiraFetcher


def test_parse_issues_from_text():
    fetcher = McpJiraFetcher.__new__(McpJiraFetcher)
    fetcher.site = "mysite"
    fetcher.cloud_id = ""
    text = "Found issues PROJ-1 and PROJ-2 in sprint"
    issues = fetcher._parse_issues_from_text(text)
    keys = {i.key for i in issues}
    assert keys == {"PROJ-1", "PROJ-2"}
