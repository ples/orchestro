"""Jira API client for fetching issues and project metadata (HTTP Basic Auth)."""

import json
import os
import re
from dataclasses import dataclass

import requests


def _extract_adf_text(adf: dict | None) -> str:
    """Extract plain text from Jira ADF (Atlassian Document Format) description."""
    if not isinstance(adf, dict):
        return str(adf or "")

    def _collect(node: dict, depth: int = 0) -> list[str]:
        parts: list[str] = []
        if "content" in node:
            for child in node["content"]:
                parts.extend(_collect(child, depth + 1))
        text = node.get("text", "")
        if text and text.strip():
            parts.append(text)
        return parts

    # The ADF structure is {type: 'doc', version: 1, content: [...]}
    lines = []
    for block in adf.get("content", []):
        block_type = block.get("type", "")
        if block_type == "paragraph":
            texts = _collect(block)
            text = "".join(texts).strip()
            if text:
                lines.append(text)
        elif block_type == "heading":
            level = block.get("attrs", {}).get("level", 2)
            texts = _collect(block)
            text = "".join(texts).strip()
            if text:
                lines.append(f"{'#' * level} {text}")
        elif block_type == "bulletList":
            # extract list items
            for item in block.get("content", []):
                for sub in item.get("content", []):
                    texts = _collect(sub)
                    text = "".join(texts).strip()
                    if text:
                        lines.append(f"- {text}")
        elif block_type == "orderedList":
            i = 1
            for item in block.get("content", []):
                for sub in item.get("content", []):
                    texts = _collect(sub)
                    text = "".join(texts).strip()
                    if text:
                        lines.append(f"{i}. {text}")
                        i += 1
        elif block_type in ("mediaSingle", "media", "image", "embed"):
            alt = block.get("attrs", {}).get("alt", "")
            if alt:
                lines.append(f"[Image: {alt}]")
        elif block_type == "codeBlock":
            texts = _collect(block)
            code = "\n".join(texts)
            if code:
                lines.append(f"```{code}```")
                lines.append("```")

    return "\n".join(lines).strip()


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
    components: tuple[str, ...] = ()
    issue_type: str | None = None


def format_jira_issue_context(issue: JiraIssue) -> str:
    """Build planner/repo-detector text from a Jira issue."""
    parts = [f"{issue.key}: {issue.title}"]
    if issue.issue_type:
        parts.append(f"Type: {issue.issue_type}")
    if issue.components:
        parts.append(f"Components: {', '.join(issue.components)}")
    if issue.body:
        parts.append(f"\nDescription:\n{issue.body.strip()}")
    return "\n".join(parts)


def _parse_components(fields: dict) -> tuple[str, ...]:
    raw = fields.get("components") or []
    names: list[str] = []
    for item in raw:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    return tuple(names)


class JiraFetcher:
    """Fetches issues and project metadata from Jira.

    Uses HTTP Basic Authentication (email + api_token), matching the virtual-pm
    scripts that work with the dmetrics Atlassian instance.
    """

    def __init__(self, token: str | None = None, site: str | None = None):
        # Read auth credentials (supports both old and new env var names)
        self.token = token or os.getenv("JIRA_API_TOKEN") or os.getenv("JIRA_TOKEN", "")
        self.email = os.getenv("JIRA_EMAIL") or os.getenv("JIRA_BASE_EMAIL", "")

        # Read and normalize site URL
        raw_site = site or os.getenv("JIRA_SITE") or os.getenv("JIRA_BASE_URL", "")
        # Strip protocol
        if raw_site.startswith("https://"):
            raw_site = raw_site[len("https://"):]
        elif raw_site.startswith("http://"):
            raw_site = raw_site[len("http://"):]
        # Keep full host as base_url since that's what we append /rest/api/3/ to
        self._base_host = raw_site if raw_site else "dmetrics.atlassian.net"
        self.base_url = f"https://{self._base_host}/rest/api/3"

        # Build a requests Session with Basic Auth (email:api_token)
        self.session = requests.Session()
        self.session.auth = (self.email, self.token) if self.email else None
        self.session.headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
        })
        if not self.session.auth:
            # Fallback: try cookie-based auth
            self.session.headers["Cookie"] = f"atlassian_token={self.token}"

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        return self.session.get(self._url(path), params=params, timeout=30)

    def _post(self, path: str, json_data: dict | None = None) -> requests.Response:
        return self.session.post(self._url(path), json=json_data, timeout=30)

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
        resp = self._post(
            "/search/jql",
            json_data={
                "jql": jql,
                "maxResults": min(max_results, 100),
                "fields": [
                    "summary",
                    "status",
                    "assignee",
                    "priority",
                    "description",
                    "components",
                    "issuetype",
                ],
            },
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Jira API error {resp.status_code}: {resp.text}")

        data = resp.json()
        issues = []
        for item in data.get("issues", []):
            fields = item.get("fields", {})
            issue_key = item["key"]
            project = fields.get("project", {}).get("key", "")
            if not project:
                project = issue_key.split("-")[0]

            assignee_data = fields.get("assignee")
            assignee = (
                assignee_data.get("displayName") or assignee_data.get("name")
                if assignee_data else None
            )
            status = fields.get("status", {}).get("name", "Unknown")
            priority = (
                fields.get("priority", {}).get("name") if fields.get("priority")
                else None
            )

            description_raw = fields.get("description", "")
            body = _extract_adf_text(description_raw) if isinstance(description_raw, dict) else str(description_raw or "")

            issue_type = (
                fields.get("issuetype", {}).get("name")
                if isinstance(fields.get("issuetype"), dict)
                else None
            )
            issues.append(JiraIssue(
                key=issue_key,
                title=fields.get("summary", ""),
                state=fields.get("status", {}).get("name", "Unknown"),
                assignee=assignee,
                body=body,
                url=f"https://{self._base_host}/browse/{issue_key}",
                project=project,
                priority=priority,
                components=_parse_components(fields),
                issue_type=issue_type,
            ))

        return issues

    def fetch_issue(self, issue_key: str) -> JiraIssue:
        """Fetch a single Jira issue by key."""
        resp = self._get(
            f"/issue/{issue_key}",
            params={
                "fields": "summary,status,assignee,priority,description,components,issuetype",
            },
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Jira API error {resp.status_code}: {resp.text}")

        data = resp.json()
        fields = data.get("fields", {})
        issue_key = data["key"]
        project = fields.get("project", {}).get("key", "")
        if not project:
            project = issue_key.split("-")[0]

        assignee_data = fields.get("assignee")
        assignee = (
            assignee_data.get("displayName") or assignee_data.get("name")
            if assignee_data else None
        )

        description_raw = fields.get("description", "")
        body = _extract_adf_text(description_raw) if isinstance(description_raw, dict) else str(description_raw or "")

        issue_type = (
            fields.get("issuetype", {}).get("name")
            if isinstance(fields.get("issuetype"), dict)
            else None
        )

        return JiraIssue(
            key=issue_key,
            title=fields.get("summary", ""),
            state=fields.get("status", {}).get("name", "Unknown"),
            assignee=assignee,
            body=body,
            url=f"https://{self._base_host}/browse/{issue_key}",
            project=project,
            priority=fields.get("priority", {}).get("name") if fields.get("priority") else None,
            components=_parse_components(fields),
            issue_type=issue_type,
        )

    def get_project_keys(self) -> list[str]:
        """Get all visible project keys."""
        resp = self._get("/project/search", params={"maxResults": 100})
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
