"""Jira API client for fetching issues and project metadata."""

import os
import re
from dataclasses import dataclass

import requests


@dataclass
class JiraIssue:
    """Represents a Jira issue fetched via the REST API."""

    key: str
    title: str
    state: str
    assignee: str | None
    body: str
    url: str
    project: str
    priority: str | None = None


class JiraFetcher:
    """Fetches issues and project metadata from Jira."""

    def __init__(self, token: str | None = None, site: str | None = None):
        self.token = token or os.getenv("JIRA_API_TOKEN", "")
        self.site = site or os.getenv("JIRA_SITE", "")
        if not self.site:
            self.site = "your-site"
        self.base_url = f"https://{self.site}.atlassian.net/rest/api/latest"

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _headers(self) -> dict:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

    @staticmethod
    def parse_jira_url(url: str) -> tuple[str, str]:
        """Parse a Jira browse URL into (project_key_or_name, issue_key).

        Handles formats:
          - https://site.atlassian.net/browse/PROJ-123
          - https://site.atlassian.net/projects/PROJ/issues/PROJ-123
        """
        match = re.search(r"/browse/([A-Za-z0-9]+(-[A-Za-z0-9]+)*)-(\d+)", url)
        if match:
            return f"{match.group(1)}-{match.group(3)}", match.group(1)

        match = re.match(
            r"https://[^.]+\.atlassian\.net/browse/([A-Za-z0-9]+(-[A-Za-z0-9]+)*)-(\d+)",
            url,
        )
        if match:
            return f"{match.group(1)}-{match.group(3)}", match.group(1)

        short = re.match(r"([A-Za-z0-9]+(-[A-Za-z0-9]+)*)-(\d+)", url)
        if short:
            return f"{short.group(1)}-{short.group(3)}", short.group(1)

        raise ValueError(f"Cannot parse Jira URL or key: {url}")

    def fetch_issues(self, jql: str, max_results: int = 50) -> list[JiraIssue]:
        """Search Jira issues using a JQL query."""
        url = self._url("/search")
        params = {
            "jql": jql,
            "maxResults": min(max_results, 100),
            "fields": "summary,status,assignee,priority,description",
        }
        resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Jira API error {resp.status_code}: {resp.text}")

        data = resp.json()
        issues = []
        for item in data.get("issues", []):
            fields = item.get("fields", {})
            issue_key = item["key"]
            project = item.get("raw", {}).get("fields.project.key", "")
            if not project:
                project = fields.get("project", {}).get("key", "")
                if not project:
                    # fallback: parse from issue_key
                    parts = issue_key.split("-")
                    project = parts[0] if parts else ""

            assignee_data = fields.get("assignee")
            assignee = assignee_data.get("displayName") or assignee_data.get("name") if assignee_data else None
            status = fields.get("status", {}).get("name", "Unknown")
            priority = fields.get("priority", {}).get("name") if fields.get("priority") else None

            issues.append(JiraIssue(
                key=issue_key,
                title=fields.get("summary", ""),
                state=status,
                assignee=assignee,
                body=fields.get("description", "") or "",
                url=f"https://{self.site}.atlassian.net/browse/{issue_key}",
                project=project,
                priority=priority,
            ))

        return issues

    def fetch_issue(self, issue_key: str) -> JiraIssue:
        """Fetch a single Jira issue by key."""
        url = self._url(f"/issue/{issue_key}")
        params = {
            "fields": "summary,status,assignee,priority,description",
        }
        resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Jira API error {resp.status_code}: {resp.text}")

        data = resp.json()
        fields = data.get("fields", {})
        issue_key = data["key"]
        project = data.get("fields", {}).get("project", {}).get("key", "")
        if not project:
            project = issue_key.split("-")[0]

        assignee_data = fields.get("assignee")
        assignee = assignee_data.get("displayName") or assignee_data.get("name") if assignee_data else None

        return JiraIssue(
            key=issue_key,
            title=fields.get("summary", ""),
            state=fields.get("status", {}).get("name", "Unknown"),
            assignee=assignee,
            body=fields.get("description", "") or "",
            url=f"https://{self.site}.atlassian.net/browse/{issue_key}",
            project=project,
            priority=fields.get("priority", {}).get("name") if fields.get("priority") else None,
        )

    def get_project_keys(self) -> list[str]:
        """Get all visible project keys."""
        url = self._url("/project/search")
        params = {"maxResults": 100}
        resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Jira API error {resp.status_code}: {resp.text}")

        return [p["key"] for p in resp.json().get("values", [])]

    def suggest_jql(self, project_key: str) -> str:
        """Generate a JQL query for open issues in a project."""
        return f'project = "{project_key}" AND status IN ("To Do", "In Progress", "Open") ORDER BY created DESC'

    def pick_issue(self, issues: list[JiraIssue]) -> JiraIssue:
        """Show an interactive numbered list of issues and return the selection."""
        if not issues:
            raise RuntimeError("No issues found in Jira.")

        print("\n=== Jira Issues ===")
        for i, issue in enumerate(issues, 1):
            assignee_str = f"@{issue.assignee}" if issue.assignee else "unassigned"
            priority_str = f"[{issue.priority}]" if issue.priority else ""
            print(f"{i}. {issue.key} - {issue.title} ({issue.state}, {assignee_str}, {priority_str})")

        while True:
            choice = input("\nSelect issue number (or 'q' to quit): ").strip()
            if choice.lower() == "q":
                print("Cancelled.")
                raise KeyboardInterrupt()
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(issues):
                    return issues[idx]
                print(f"Invalid number. Choose 1-{len(issues)}.")
            except ValueError:
                print("Please enter a number or 'q' to quit.")
