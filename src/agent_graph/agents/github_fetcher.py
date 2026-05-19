"""GitHub API client for fetching issues and repo structure."""

import os
import re
from dataclasses import dataclass

import requests


@dataclass
class GitHubIssue:
    """Represents a GitHub issue fetched via the API."""

    number: int
    title: str
    state: str
    assignee: str | None
    body: str
    url: str


class GitHubFetcher:
    """Fetches issues and repo structure from GitHub."""

    API_BASE = "https://api.github.com/repos/{owner}/{repo}/issues"
    REPO_API = "https://api.github.com/repos/{owner}/{repo}"

    def __init__(self, token: str | None = None):
        self.token = token or os.getenv("GITHUB_TOKEN", "")

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/vnd.github.v3+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    @staticmethod
    def parse_repo_remote(
        target_repo_path: str = "",
        github_issue_url: str = "",
    ) -> tuple[str, str]:
        """Resolve owner/repo from a git remote URL or GitHub issue URL."""
        for source in (target_repo_path, github_issue_url):
            if not source:
                continue
            if source.startswith("http") or source.startswith("github:"):
                try:
                    owner, repo, _ = GitHubFetcher(token="").parse_github_url(source)
                    return owner, repo.removesuffix(".git")
                except ValueError:
                    pass
            match = re.match(
                r"(?:https://github\.com/|git@github\.com:)([^/]+)/([^/.\s]+)",
                source,
            )
            if match:
                repo_name = match.group(2).removesuffix(".git")
                return match.group(1), repo_name
        raise ValueError(
            f"Cannot resolve GitHub owner/repo from: {target_repo_path!r} / {github_issue_url!r}"
        )

    def get_default_branch(self, owner: str, repo: str) -> str:
        override = os.getenv("GITHUB_PR_BASE_BRANCH", "")
        if override:
            return override
        url = self.REPO_API.format(owner=owner, repo=repo)
        resp = requests.get(url, headers=self._headers(), timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"GitHub API error {resp.status_code}: {resp.text}")
        return resp.json()["default_branch"]

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str,
    ) -> str:
        url = f"{self.REPO_API.format(owner=owner, repo=repo)}/pulls"
        resp = requests.post(
            url,
            headers=self._headers(),
            json={"title": title, "head": head, "base": base, "body": body},
            timeout=30,
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"GitHub API error {resp.status_code}: {resp.text}")
        return resp.json()["html_url"]

    def fetch_issues(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        per_page: int = 50,
    ) -> list[GitHubIssue]:
        """Fetch issues from a GitHub repository."""
        url = self.API_BASE.format(owner=owner, repo=repo)
        params = {
            "state": state,
            "per_page": min(per_page, 100),
            "sort": "created",
            "direction": "desc",
        }
        resp = requests.get(url, params=params, headers=self._headers(), timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"GitHub API error {resp.status_code}: {resp.text}")

        issues = []
        for item in resp.json():
            assignee_data = item.get("assignee") or {}
            assignee = assignee_data.get("login") if assignee_data else None
            issues.append(GitHubIssue(
                number=item["number"],
                title=item["title"],
                state=item["state"],
                assignee=assignee,
                body=item.get("body") or "",
                url=item["html_url"],
            ))

        return issues

    def parse_github_url(self, url: str) -> tuple[str, str, int | None]:
        """Parse a GitHub issue/PR URL into (owner, repo, number)."""
        patterns = [
            r"https://github\.com/([^/]+)/([^/]+)/issues/(\d+)",
            r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)",
            r"github:([^/]+)/([^/]+)/issues/(\d+)",
        ]
        for pattern in patterns:
            match = re.match(pattern, url)
            if match:
                return match.group(1), match.group(2), int(match.group(3))

        repo_only = re.match(r"https://github\.com/([^/]+)/([^/]+)/?$", url)
        if repo_only:
            return repo_only.group(1), repo_only.group(2), None

        short = re.match(r"github:([^/]+)/([^/]+)", url)
        if short:
            return short.group(1), short.group(2), None

        raise ValueError(f"Cannot parse GitHub URL: {url}")

    def pick_issue(self, issues: list[GitHubIssue]) -> GitHubIssue:
        """Show an interactive numbered list of issues and return the selection."""
        if not issues:
            raise RuntimeError("No issues found in the repository.")

        print("\n=== GitHub Issues ===")
        for i, issue in enumerate(issues, 1):
            assignee_str = f"@{issue.assignee}" if issue.assignee else "unassigned"
            print(f"{i}. #{issue.number} - {issue.title} ({issue.state}, {assignee_str})")

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

    def fetch_repo_tree(self, owner: str, repo: str, path: str = "", token: str | None = None) -> str:
        """Fetch tree structure of a GitHub repository.

        Args:
            owner: Organization or user owner.
            repo: Repository name.
            path: Subdirectory path to fetch.
            token: Optional API token (defaults to env).

        Returns:
            Formatted string of repository file tree.
        """
        api_token = token or self.token
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{path}"
        headers = {"Accept": "application/vnd.github.v3+json"}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"

        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return f"(Could not fetch repo tree: {resp.status_code})"

        tree_data = resp.json()
        lines = [f"Path: {path}/" if path else "Repository tree:"]

        for item in tree_data.get("tree", []):
            indent = "  " + ("  " * (len(path.split("/")) - 1)) if path else ""
            if item["type"] == "tree":
                lines.append(f"{indent}📁 {item['path']}/")
                sub_prefix = indent + ("  " if path else "")
                lines.extend(self._fetch_branch_entries(owner, repo, f"{path}/{item['path']}" if path else item["path"], sub_prefix, token))
            elif item["type"] == "blob":
                rel_path = f"{path}/{item['path']}" if path else item["path"]
                lines.append(f"{indent}📄 {rel_path} ({item['size']} bytes)")

        return "\n".join(lines)

    def _fetch_branch_entries(self, owner: str, repo: str, path: str, indent: str, token: str | None = None) -> list[str]:
        """Fetch direct children of a directory."""
        api_token = token or self.token
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{path}"
        headers = {"Accept": "application/vnd.github.v3+json"}
        if api_token:
            headers["Authorization"] = f"Bearer {api_token}"

        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return [f"{indent}(could not fetch: {resp.status_code})"]

        tree_data = resp.json()
        lines = []
        for item in tree_data.get("tree", []):
            if item["type"] == "tree":
                lines.append(f"{indent}📁 {item['path']}/")
            elif item["type"] == "blob":
                lines.append(f"{indent}📄 {item['path']} ({item['size']} bytes)")
        return lines
