"""Per-repo PR creator: creates a PR for each repo that has changes."""

import os
import re
import subprocess
from datetime import datetime

from agent_graph.agents.bitbucket_fetcher import BitbucketFetcher
from agent_graph.agents.github_fetcher import GitHubFetcher
from agent_graph.git_utils import (
    commit_count_since,
    has_changes_since,
    has_uncommitted_changes,
)
from agent_graph.state import RepoRecord, TaskState

from .base import BaseAgent


class PrAggregatorAgent(BaseAgent):
    """Creates pull requests for each repository that has changes."""

    name = "pr_aggregator"

    def _execute(self, state: TaskState) -> dict:
        target_repos = state.get("target_repos", [])
        if not target_repos:
            return self._execute_single_repo(state)

        all_pull_urls: list[str] = []
        all_errors: list[str] = []

        print(f"\n[PR Aggregator] Creating PRs for {len(target_repos)} repository(ies)...")

        for i, repo in enumerate(target_repos):
            skip_reason = repo.get("pr_skip_reason", "")
            if skip_reason == "no_changes":
                continue
            if repo.get("pr_error"):
                all_errors.append(f"{repo.get('target_repo_path', '?')}: {repo['pr_error']}")
                continue

            print(f"  [Repo {i+1}/{len(target_repos)}] Target: {repo.get('target_repo_path', '?')}")

            pr_result = self._create_pr(state, repo)
            pr_url = pr_result.get("pr_url", "")
            if pr_url:
                all_pull_urls.append(pr_url)
            pr_err = pr_result.get("pr_error", "")
            if pr_err:
                all_errors.append(f"{repo.get('target_repo_path', '?')}: {pr_err}")

        if all_pull_urls:
            print(f"  [PR Aggregator] Created {len(all_pull_urls)} pull request(s).")
        if all_errors:
            print(f"  [PR Aggregator] {len(all_errors)} repo(s) had PR errors.")

        return {
            "pr_url": "\n".join(all_pull_urls),
            "pr_error": "\n".join(all_errors) if all_errors else "",
        }

    # -- per-repo PR creation ------------------------------------------------

    def _create_pr(self, state: TaskState, repo: RepoRecord) -> dict:
        target = repo.get("target_repo_path", "")
        work_repo = repo.get("work_repo_path", "")
        baseline = repo.get("repo_baseline_sha", "")

        if not work_repo:
            return {"pr_url": "", "pr_error": "No work repo"}
        if not has_changes_since(work_repo, baseline):
            return {"pr_url": "", "pr_error": "", "pr_skip_reason": "no_changes"}
        if not target:
            return {"pr_url": "", "pr_error": "No target repo in repo record"}

        issue_text = state.get("issue", "").strip().split("\n")[0][:72]
        issue_number = None
        for url_key in ("github_issue_url",):
            url = state.get(url_key, "")
            if url:
                issue_number = self._extract_issue_number(url)
                break

        if "github.com" in target or any(
            "github.com" in state.get(k, "") for k in ("github_issue_url", "bitbucket_issue_url", "jira_issue_url")
        ):
            return self._make_github_pr(state, repo, issue_text, issue_number, target)

        return self._make_bitbucket_pr(state, repo, issue_text)

    # -- GitHub PR -----------------------------------------------------------

    def _make_github_pr(self, state: TaskState, repo: RepoRecord,
                        issue_text: str, issue_number: int | None,
                        target: str) -> dict:
        work_repo = repo.get("work_repo_path", "")
        baseline = repo.get("repo_baseline_sha", "")
        token = os.getenv("GITHUB_TOKEN", "")
        if not token:
            return {"pr_url": "", "pr_error": "GITHUB_TOKEN required"}

        owner, repo_name = self._resolve_owner_repo(state, platform_github=True)
        if not owner:
            return {"pr_url": "", "pr_error": "Could not resolve GitHub owner/repo"}

        branch = self._branch_name(state, issue_number)
        auth_remote = f"https://x-access-token:{token}@github.com/{owner}/{repo_name}.git"
        git_name = os.getenv("GIT_AUTHOR_NAME", "Agent Graph")
        git_email = os.getenv("GIT_AUTHOR_EMAIL", "agent@users.noreply.github.com")
        commit_msg = f"Fix #{issue_number}: {issue_text}" if issue_number else issue_text or "Agent implementation"

        try:
            self._git(work_repo, "remote", "set-url", "origin", auth_remote)
            self._git(work_repo, "config", "user.name", git_name)
            self._git(work_repo, "config", "user.email", git_email)
            self._git(work_repo, "checkout", "-b", branch)
            if has_uncommitted_changes(work_repo):
                self._git(work_repo, "add", "-A")
                self._git(work_repo, "commit", "-m", commit_msg)
            elif commit_count_since(work_repo, baseline) > 0:
                pass  # using existing commits
            self._git(work_repo, "push", "-u", "origin", branch)
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e)
            return {"pr_url": "", "pr_error": f"Git push failed: {err}"}

        fetcher = GitHubFetcher(token=token)
        try:
            base = fetcher.get_default_branch(owner, repo_name)
            title = self._gh_title(issue_text, issue_number)
            body = self._gh_body(state, repo, issue_number)
            pr_url = fetcher.create_pull_request(
                owner, repo_name, title=title, head=branch, base=base, body=body
            )
        except RuntimeError as e:
            return {"pr_url": "", "pr_error": f"PR API error: {e}"}

        return {"pr_url": pr_url, "pr_error": ""}

    # -- Bitbucket PR --------------------------------------------------------

    def _make_bitbucket_pr(self, state: TaskState, repo: RepoRecord,
                           issue_text: str) -> dict:
        work_repo = repo.get("work_repo_path", "")
        baseline = repo.get("repo_baseline_sha", "")
        token = os.getenv("BITBUCKET_TOKEN", "")
        if not token:
            return {"pr_url": "", "pr_error": "BITBUCKET_TOKEN required"}

        target = repo.get("target_repo_path", "")
        owner, repo_name = self._resolve_owner_repo(state, platform_github=False)
        if not owner:
            return {"pr_url": "", "pr_error": "Could not resolve Bitbucket owner/repo"}

        branch = self._branch_name(state, None)
        auth_remote = f"https://x-token-auth:{token}@bitbucket.org/{owner}/{repo_name}.git"
        git_name = os.getenv("GIT_AUTHOR_NAME", "Agent Graph")
        git_email = os.getenv("GIT_AUTHOR_EMAIL", "agent@users.noreply.github.com")
        commit_msg = issue_text or "Agent implementation"

        try:
            self._git(work_repo, "remote", "set-url", "origin", auth_remote)
            self._git(work_repo, "config", "user.name", git_name)
            self._git(work_repo, "config", "user.email", git_email)
            self._git(work_repo, "checkout", "-b", branch)
            if has_uncommitted_changes(work_repo):
                self._git(work_repo, "add", "-A")
                self._git(work_repo, "commit", "-m", commit_msg)
            elif commit_count_since(work_repo, baseline) > 0:
                pass
            self._git(work_repo, "push", "-u", "origin", branch)
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e)
            return {"pr_url": "", "pr_error": f"Git push failed: {err}"}

        fetcher = BitbucketFetcher(token=token)
        try:
            base = fetcher.get_default_branch(owner, repo_name)
            title = issue_text[:100] or "Agent changes"
            body = self._bb_body(state, repo)
            pr_url = fetcher.create_pull_request(
                owner, repo_name, title=title, head=branch, base=base, body=body
            )
        except RuntimeError as e:
            return {"pr_url": "", "pr_error": f"PR API error: {e}"}

        return {"pr_url": pr_url, "pr_error": ""}

    # -- Shared helpers ------------------------------------------------------

    def _resolve_owner_repo(self, state: TaskState, platform_github: bool = True) -> tuple[str, str]:
        """Resolve owner/repo from repo records or legacy state fields."""
        target_repos = state.get("target_repos", [])
        if target_repos:
            for repo in target_repos:
                target = repo.get("target_repo_path", "")
                url = repo.get("bitbucket_issue_url", "") or repo.get("github_issue_url", "") or repo.get("jira_issue_url", "")
                if platform_github:
                    try:
                        return GitHubFetcher.parse_repo_remote(target, url or state.get("github_issue_url", ""))
                    except ValueError:
                        pass
                else:
                    try:
                        return BitbucketFetcher.parse_repo_remote(target, url)
                    except ValueError:
                        pass

        # Fallback: single-repo legacy state
        target = state.get("target_repo_path", "")
        g_url = state.get("github_issue_url", "")
        bb_url = state.get("bitbucket_issue_url", "")
        if platform_github:
            try:
                return GitHubFetcher.parse_repo_remote(target, g_url)
            except ValueError:
                return ("", "")
        try:
            return BitbucketFetcher.parse_repo_remote(target, bb_url)
        except ValueError:
            return ("", "")

    @staticmethod
    def _extract_issue_number(url: str) -> int | None:
        match = re.search(r"/issues/(\d+)", url)
        return int(match.group(1)) if match else None

    @staticmethod
    def _branch_name(state: TaskState, issue_number: int | None) -> str:
        jira_url = state.get("jira_issue_url", "")
        if jira_url:
            match = re.search(r"/browse/([A-Za-z0-9]+(-[A-Za-z0-9]+)*)-(\d+)", jira_url)
            if match:
                return f"agent/issue-{match.group(1)}-{match.group(3)}"
        if issue_number:
            return f"agent/issue-{issue_number}"
        return f"agent/run-{datetime.now(datetime.UTC).strftime('%Y%m%d%H%M%S')}"

    @staticmethod
    def _git(repo_path: str, *args: str) -> None:
        subprocess.run(["git", "-C", repo_path, *args], check=True, capture_output=True)

    @staticmethod
    def _gh_title(issue_text: str, issue_number: int | None) -> str:
        line = issue_text.strip().split("\n")[0][:80]
        if issue_number:
            return f"[#{issue_number}] {line}" if line else f"Agent changes for issue #{issue_number}"
        return line or "Agent implementation"

    @staticmethod
    def _gh_body(state: TaskState, repo: RepoRecord, issue_number: int | None) -> str:
        repo_url = repo.get("target_repo_path", "")
        parts = []
        if issue_number:
            parts.append(f"Fixes #{issue_number}")
        if repo_url:
            parts.append(f"\n\n**Repository:** {repo_url}")
        if state.get("plan"):
            parts.append(f"\n## Plan\n\n{state['plan']}")
        if repo.get("repo_summary"):
            parts.append(f"\n## Implementation\n\n{repo['repo_summary']}")
        if repo.get("change_stat"):
            parts.append(f"\n## Changes\n\n{repo['change_stat']}")
        return "\n".join(parts)

    @staticmethod
    def _bb_body(state: TaskState, repo: RepoRecord) -> str:
        repo_url = repo.get("target_repo_path", "")
        parts = []
        if repo_url:
            parts.append(f"Repository: {repo_url}")
        if state.get("plan"):
            parts.append(f"\n## Plan\n\n{state['plan']}")
        if repo.get("repo_summary"):
            parts.append(f"\n## Implementation\n\n{repo['repo_summary']}")
        return "\n".join(parts)

    # -- backwards compatibility fallback ------------------------------------

    def _execute_single_repo(self, state: TaskState) -> dict:
        work_repo = state.get("work_repo_path", "")
        target = state.get("target_repo_path", "")
        if not work_repo:
            return {"pr_url": "", "pr_error": "", "pr_skip_reason": "No work repo"}
        if not has_changes_since(work_repo, state.get("repo_baseline_sha", "")):
            return {"pr_url": "", "pr_error": "", "pr_skip_reason": "no_changes"}
        if not target:
            return {"pr_url": "", "pr_error": "", "pr_skip_reason": "No target repo"}

        if "github.com" in target or "github.com" in state.get("github_issue_url", ""):
            return self._make_github_fallback(state)
        return self._make_bb_fallback(state)

    def _make_github_fallback(self, state: TaskState) -> dict:
        token = os.getenv("GITHUB_TOKEN", "")
        if not token:
            return {"pr_url": "", "pr_error": "GITHUB_TOKEN required"}
        target = state.get("target_repo_path", "")
        g_url = state.get("github_issue_url", "")
        owner, repo_name = GitHubFetcher.parse_repo_remote(target, g_url)
        if not owner:
            return {"pr_url": "", "pr_error": "Could not resolve owner/repo"}
        work_repo = state.get("work_repo_path", "")
        issue_number = self._extract_issue_number(g_url)
        branch = f"agent/issue-{issue_number}" if issue_number else f"agent/run-{datetime.now(datetime.UTC).strftime('%Y%m%d%H%M%S')}"
        auth_remote = f"https://x-access-token:{token}@github.com/{owner}/{repo_name}.git"
        try:
            self._git(work_repo, "remote", "set-url", "origin", auth_remote)
            self._git(work_repo, "checkout", "-b", branch)
            issue = state.get("issue", "")[:72]
            self._git(work_repo, "add", "-A")
            self._git(
                work_repo, "commit",
                "-m", f"Fix #{issue_number}: {issue}" if issue_number else issue,
            )
            self._git(work_repo, "push", "-u", "origin", branch)
        except subprocess.CalledProcessError:
            return {"pr_url": "", "pr_error": "Git push failed"}
        fetcher = GitHubFetcher(token=token)
        try:
            base = fetcher.get_default_branch(owner, repo_name)
            issue_text = state.get("issue", "")[:80]
            title = f"[#{issue_number}] {issue_text}" if issue_number else issue_text
            pr_url = fetcher.create_pull_request(owner, repo_name, title=title, head=branch, base=base, body=state.get("plan", "") or "")
            return {"pr_url": pr_url, "pr_error": ""}
        except RuntimeError as e:
            return {"pr_url": "", "pr_error": f"PR API error: {e}"}

    def _make_bb_fallback(self, state: TaskState) -> dict:
        token = os.getenv("BITBUCKET_TOKEN", "")
        if not token:
            return {"pr_url": "", "pr_error": "BITBUCKET_TOKEN required"}
        target = state.get("target_repo_path", "")
        owner, repo_name = BitbucketFetcher.parse_repo_remote(target, target)
        if not owner:
            return {"pr_url": "", "pr_error": "Could not resolve owner/repo"}
        work_repo = state.get("work_repo_path", "")
        branch = f"agent/run-{datetime.now(datetime.UTC).strftime('%Y%m%d%H%M%S')}"
        auth_remote = f"https://x-token-auth:{token}@bitbucket.org/{owner}/{repo_name}.git"
        try:
            self._git(work_repo, "remote", "set-url", "origin", auth_remote)
            self._git(work_repo, "checkout", "-b", branch)
            issue = state.get("issue", "")[:72]
            self._git(work_repo, "add", "-A")
            self._git(work_repo, "commit", "-m", issue or "Agent changes")
            self._git(work_repo, "push", "-u", "origin", branch)
        except subprocess.CalledProcessError:
            return {"pr_url": "", "pr_error": "Git push failed"}
        fetcher = BitbucketFetcher(token=token)
        try:
            base = fetcher.get_default_branch(owner, repo_name)
            issue_text = state.get("issue", "")[:100]
            pr_url = fetcher.create_pull_request(
                owner, repo_name, title=issue_text or "Agent changes",
                head=branch, base=base, body=state.get("plan", "") or "",
            )
            return {"pr_url": pr_url, "pr_error": ""}
        except RuntimeError as e:
            return {"pr_url": "", "pr_error": f"PR API error: {e}"}
