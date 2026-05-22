"""Jira issue fetcher backed by Atlassian MCP (optional alternative to REST)."""

from __future__ import annotations

import os
import re

from agent_graph.agents.jira_fetcher import JiraIssue, _parse_components
from agent_graph.mcp.client import McpToolClient, parse_json_tool_result

ATLASSIAN_SERVER = "atlassian"
ATLASSIAN_MCP_ALT = "atlassian_mcp"


class McpJiraFetcher:
    """Fetch Jira issues via Atlassian MCP tools."""

    def __init__(
        self,
        client: McpToolClient | None = None,
        cloud_id: str | None = None,
        site: str | None = None,
    ) -> None:
        self._client = client or McpToolClient()
        self.cloud_id = cloud_id or os.getenv("JIRA_CLOUD_ID", "")
        self.site = site or os.getenv("JIRA_SITE", "")

    def _server_name(self) -> str:
        for name in (ATLASSIAN_SERVER, ATLASSIAN_MCP_ALT, "atlassian_mcp"):
            if self._client.has_server(name):
                return name
        for name in self._client.server_names:
            if "atlassian" in name:
                return name
        msg = "Atlassian MCP server is not configured or enabled"
        raise RuntimeError(msg)

    def _resolve_cloud_id(self) -> str:
        if self.cloud_id:
            return self.cloud_id
        if self.site:
            if self.site.startswith("http"):
                return self.site
            return f"https://{self.site}.atlassian.net"
        msg = "JIRA_CLOUD_ID or JIRA_SITE is required for MCP Jira fetch"
        raise RuntimeError(msg)

    @staticmethod
    def parse_jira_url(url: str) -> tuple[str, str]:
        from agent_graph.agents.jira_fetcher import JiraFetcher

        return JiraFetcher.parse_jira_url(url)

    def suggest_jql(self, project_key: str) -> str:
        from agent_graph.agents.jira_fetcher import JiraFetcher

        return JiraFetcher.suggest_jql(project_key)

    def fetch_issues(self, jql: str, max_results: int = 50) -> list[JiraIssue]:
        server = self._server_name()
        cloud_id = self._resolve_cloud_id()
        text = self._client.call_tool_sync(
            server,
            "searchJiraIssuesUsingJql",
            {
                "cloudId": cloud_id,
                "jql": jql,
                "maxResults": min(max_results, 100),
            },
        )
        return self._parse_issues_from_mcp(text)

    def fetch_issue(self, issue_key: str) -> JiraIssue:
        server = self._server_name()
        cloud_id = self._resolve_cloud_id()
        text = self._client.call_tool_sync(
            server,
            "getJiraIssue",
            {
                "cloudId": cloud_id,
                "issueIdOrKey": issue_key,
            },
        )
        issues = self._parse_issues_from_mcp(text)
        if issues:
            return issues[0]
        return JiraIssue(
            key=issue_key,
            title=issue_key,
            state="Unknown",
            assignee=None,
            body=text,
            url=self._issue_url(issue_key),
            project=issue_key.split("-")[0] if "-" in issue_key else "",
        )

    def _issue_url(self, key: str) -> str:
        site = self.site.replace("https://", "").replace("http://", "")
        if site.endswith(".atlassian.net"):
            host = site
        elif site:
            host = f"{site}.atlassian.net"
        else:
            host = "atlassian.net"
        return f"https://{host}/browse/{key}"

    def _parse_issues_from_mcp(self, text: str) -> list[JiraIssue]:
        data = parse_json_tool_result(text)
        if isinstance(data, dict):
            raw_issues = data.get("issues") or data.get("values") or []
            if isinstance(raw_issues, list) and raw_issues:
                return [self._issue_from_dict(item) for item in raw_issues if isinstance(item, dict)]

        return self._parse_issues_from_text(text)

    def _issue_from_dict(self, item: dict) -> JiraIssue:
        key = str(item.get("key", item.get("issueKey", "")))
        fields = item.get("fields", item)
        if not isinstance(fields, dict):
            fields = {}
        title = str(fields.get("summary", item.get("summary", key)))
        status = fields.get("status", {})
        state = status.get("name", "Unknown") if isinstance(status, dict) else str(status)
        assignee_data = fields.get("assignee")
        assignee = None
        if isinstance(assignee_data, dict):
            assignee = assignee_data.get("displayName") or assignee_data.get("name")
        priority_data = fields.get("priority")
        priority = None
        if isinstance(priority_data, dict):
            priority = priority_data.get("name")
        body = str(fields.get("description", "") or "")
        project = str(fields.get("project", {}).get("key", key.split("-")[0]))
        issue_type_data = fields.get("issuetype")
        issue_type = (
            issue_type_data.get("name")
            if isinstance(issue_type_data, dict)
            else None
        )
        return JiraIssue(
            key=key,
            title=title,
            state=state,
            assignee=assignee,
            body=body,
            url=self._issue_url(key),
            project=project,
            priority=priority,
            components=_parse_components(fields),
            issue_type=issue_type,
        )

    def _parse_issues_from_text(self, text: str) -> list[JiraIssue]:
        issues: list[JiraIssue] = []
        for match in re.finditer(r"\b([A-Z][A-Z0-9]+-\d+)\b", text):
            key = match.group(1)
            issues.append(
                JiraIssue(
                    key=key,
                    title=key,
                    state="Unknown",
                    assignee=None,
                    body=text[:2000],
                    url=self._issue_url(key),
                    project=key.split("-")[0],
                )
            )
        seen: set[str] = set()
        unique: list[JiraIssue] = []
        for issue in issues:
            if issue.key not in seen:
                seen.add(issue.key)
                unique.append(issue)
        return unique

    def pick_issue(self, issues: list[JiraIssue]) -> JiraIssue:
        from agent_graph.agents.jira_fetcher import JiraFetcher

        return JiraFetcher.pick_issue(issues)
