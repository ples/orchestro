"""Bitbucket API client for fetching issues and repo metadata."""

import os
import re
from dataclasses import dataclass

import requests

from agent_graph.repo_clone import bitbucket_request_kwargs


@dataclass
class BitbucketIssue:
    """Represents a Bitbucket issue (or linked Jira issue)."""

    title: str
    state: str
    assignee: str | None
    body: str
    url: str
    id: int | None = None
    priority: str | None = None


class BitbucketFetcher:
    """Fetches issues and repo metadata from Bitbucket."""

    API_BASE = "https://api.bitbucket.org/2.0/repositories/{owner}/{repo}"

    def __init__(self, token: str | None = None):
        self.token = token or os.getenv("BITBUCKET_TOKEN", "")

    def _request_kwargs(self) -> dict:
        if self.token and self.token != os.getenv("BITBUCKET_TOKEN", ""):
            return {
                "headers": {
                    "Accept": "application/json",
                    "Authorization": f"Bearer {self.token}",
                },
                "timeout": 30,
            }
        return bitbucket_request_kwargs()

    @staticmethod
    def parse_repo_remote(target_repo_path: str = "", bitbucket_issue_url: str = "") -> tuple[str, str]:
        """Resolve owner/repo from a git remote URL or Bitbucket issue URL."""
        for source in (target_repo_path, bitbucket_issue_url):
            if not source:
                continue
            if source.startswith("http") or source.startswith("bitbucket:"):
                try:
                    owner, repo = BitbucketFetcher.parse_issue_url(source)
                    return owner, repo.removesuffix(".git")
                except ValueError:
                    pass
            match = re.match(
                r"(?:https://bitbucket\.org/|git@bitbucket\.org:)([^/]+)/([^/.\s]+)",
                source,
            )
            if match:
                repo_name = match.group(2).removesuffix(".git")
                return match.group(1), repo_name
        raise ValueError(
            f"Cannot resolve Bitbucket owner/repo from: {target_repo_path!r} / {bitbucket_issue_url!r}"
        )

    @staticmethod
    def parse_issue_url(url: str) -> tuple[str, str]:
        """Parse a Bitbucket issue/PR URL or branch URL into (owner, repo)."""
        match = re.match(
            r"https://bitbucket\.org/([^/]+)/([^/]+)/issues/(\d+)",
            url,
        )
        if match:
            return match.group(1), match.group(2)

        match = re.match(
            r"https://bitbucket\.org/([^/]+)/([^/]+)/pull-requests/(\d+)",
            url,
        )
        if match:
            return match.group(1), match.group(2)

        match = re.match(
            r"https://bitbucket\.org/([^/]+)/([^/]+)/src/(\S+)",
            url,
        )
        if match:
            return match.group(1), match.group(2)

        raise ValueError(f"Cannot parse Bitbucket URL: {url}")

    def get_default_branch(self, owner: str, repo: str) -> str:
        """Get the repository default branch (master/main)."""
        override = os.getenv("BITBUCKET_PR_BASE_BRANCH", "")
        if override:
            return override

        url = self.API_BASE.format(owner=owner, repo=repo)
        resp = requests.get(url, **self._request_kwargs())
        if resp.status_code in (200, 201):
            mainbranch = resp.json().get("mainbranch") or {}
            name = (mainbranch.get("name") or "").strip()
            if name:
                return name

        for candidate in ("master", "main"):
            if self._branch_exists(owner, repo, candidate):
                return candidate

        raise RuntimeError(
            f"Could not resolve default branch for {owner}/{repo} "
            "(set BITBUCKET_PR_BASE_BRANCH)"
        )

    def _branch_exists(self, owner: str, repo: str, branch: str) -> bool:
        url = (
            f"{self.API_BASE.format(owner=owner, repo=repo)}"
            f"/refs/branches/{branch}"
        )
        resp = requests.get(url, **self._request_kwargs())
        return resp.status_code in (200, 201)

    def fetch_issues(self, owner: str, repo: str, per_page: int = 10) -> list[BitbucketIssue]:
        """Fetch issues from a Bitbucket repository."""
        url = f"{self.API_BASE.format(owner=owner, repo=repo)}/issues"
        params = {
            "state": "all",
            "pagelen": min(per_page, 100),
            "sort": "-created",
        }
        resp = requests.get(url, params=params, **self._request_kwargs())
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Bitbucket API error {resp.status_code}: {resp.text}")

        data = resp.json()
        issues = []
        for item in data.get("values", []):
            assignee_data = item.get("assignee")
            if not assignee_data:
                assignee_data = item.get("user")
            assignee = assignee_data.get("username") or assignee_data.get("display_name") if assignee_data else None

            state = item.get("state", {}).get("name", "Open")

            link_expanded = item.get("links", {}).get("jira", {}).get("issues", [])
            if link_expanded:
                jira_data = link_expanded[0].get("fields", {}) if link_expanded else {}
                jira_key = link_expanded[0].get("key", "") if link_expanded else ""
                title = jira_data.get("summary", item.get("title", ""))
                priority = jira_data.get("priority", {}).get("name") if jira_data.get("priority") else None
                issue_url = jira_data.get("self", "")
                if jira_key:
                    issue_url = f"https://{os.getenv('JIRA_SITE', 'your-site')}.atlassian.net/browse/{jira_key}"
            else:
                title = item.get("title", "")
                priority = None
                issue_url = item.get("links", {}).get("html", {}).get("href", "")

            issues.append(BitbucketIssue(
                id=item.get("id"),
                title=title,
                state=state,
                assignee=assignee,
                body=item.get("content", {}).get("raw", "") or "",
                url=issue_url or f"https://bitbucket.org/{owner}/{repo}/issues/{item.get('id')}",
                priority=priority,
            ))

        return issues

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str,
    ) -> str:
        """Create a Bitbucket pull request."""
        url = f"{self.API_BASE.format(owner=owner, repo=repo)}/pullrequests"

        # Check for reviewers
        reviewers = []
        review_names = os.getenv("BITBUCKET_REVIEWERS", "")
        for name in [r.strip() for r in review_names.split(",") if r.strip()]:
            reviewers.append({"nickname": name})

        payload = {
            "title": title,
            "source": {"branch": {"name": head}},
            "destination": {"branch": {"name": base}},
            "message": body[:500] if body else "",
        }
        if reviewers:
            payload["reviewers"] = reviewers

        resp = requests.post(url, json=payload, **self._request_kwargs())
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"Bitbucket API error {resp.status_code}: {resp.text}")

        return resp.json()["links"]["html"]["href"]

    def pick_issue(self, issues: list[BitbucketIssue]) -> BitbucketIssue:
        """Show an interactive numbered list of issues and return the selection."""
        if not issues:
            raise RuntimeError("No issues found in Bitbucket.")

        print("\n=== Bitbucket Issues ===")
        for i, issue in enumerate(issues, 1):
            assignee_str = f"@{issue.assignee}" if issue.assignee else "unassigned"
            priority_str = f"[{issue.priority}]" if issue.priority else ""
            print(f"{i}. #{issue.id} - {issue.title} ({issue.state}, {assignee_str}, {priority_str})")

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
