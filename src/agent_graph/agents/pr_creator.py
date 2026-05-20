"""Creates a pull request from the executor worktree (GitHub, Bitbucket)."""

import os
import re
import subprocess
from datetime import datetime, timezone

from agent_graph.agents.bitbucket_fetcher import BitbucketFetcher
from agent_graph.agents.github_fetcher import GitHubFetcher
from agent_graph.exceptions import PrCreationError
from agent_graph.git_utils import (
    change_summary,
    commit_count_since,
    has_changes_since,
    has_uncommitted_changes,
)
from agent_graph.state import TaskState

from .base import BaseAgent


class PrCreatorAgent(BaseAgent):
    """Commits changes, pushes a branch, and opens a GitHub PR."""

    name = "pr_creator"

    def _execute(self, state: TaskState) -> dict:
        print("\n[PR Creator]")

        work_repo_path = state.get("work_repo_path", "")
        baseline_sha = state.get("repo_baseline_sha", "")

        if not work_repo_path:
            reason = "No work repository path from executor."
            print(f"  Skipping PR: {reason}")
            return {"pr_url": "", "pr_error": "", "pr_skip_reason": reason}

        info = change_summary(work_repo_path, baseline_sha)
        print(
            f"  Git state: baseline={info['baseline_sha']}, "
            f"uncommitted={info['uncommitted']}, "
            f"commits_since_baseline={info['commits_since_baseline']}"
        )

        if not has_changes_since(work_repo_path, baseline_sha):
            reason = (
                "No changes detected since workflow baseline.\n"
                f"  uncommitted={info['uncommitted']}, "
                f"commits_since_baseline={info['commits_since_baseline']}, "
                f"baseline={info['baseline_sha']}"
            )
            print(f"  Skipping PR: {reason}")
            if info["diff_stat"] != "(no diff)":
                print(f"  Diff stat: {info['diff_stat']}")
            return {"pr_url": "", "pr_error": "", "pr_skip_reason": reason}

        token = os.getenv("GITHUB_TOKEN", "")
        if not token:
            raise PrCreationError("GITHUB_TOKEN is required to create a pull request")

        target_repo = state.get("target_repo_path", "")
        github_issue_url = state.get("github_issue_url", "")
        try:
            owner, repo = GitHubFetcher.parse_repo_remote(target_repo, github_issue_url)
        except ValueError as e:
            raise PrCreationError(str(e)) from e

        issue_number = self._issue_number(github_issue_url)
        branch = (
            f"agent/issue-{issue_number}"
            if issue_number
            else f"agent/run-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        )

        auth_remote = (
            f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
        )
        git_name = os.getenv("GIT_AUTHOR_NAME", "Agent Graph")
        git_email = os.getenv("GIT_AUTHOR_EMAIL", "agent@users.noreply.github.com")
        commit_msg = self._commit_message(state, issue_number)

        try:
            self._git(work_repo_path, "remote", "set-url", "origin", auth_remote)
            self._git(work_repo_path, "config", "user.name", git_name)
            self._git(work_repo_path, "config", "user.email", git_email)
            self._git(work_repo_path, "checkout", "-b", branch)
            if has_uncommitted_changes(work_repo_path):
                self._git(work_repo_path, "add", "-A")
                self._git(work_repo_path, "commit", "-m", commit_msg)
            elif commit_count_since(work_repo_path, baseline_sha) > 0:
                print(
                    f"  Using {commit_count_since(work_repo_path, baseline_sha)} "
                    "commit(s) already made by the agent"
                )
            self._git(work_repo_path, "push", "-u", "origin", branch)
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or str(e))
            print(f"  Git error: {err}")
            return {"pr_url": "", "pr_error": f"Git push failed: {err}", "pr_skip_reason": ""}

        fetcher = GitHubFetcher(token=token)
        try:
            base = fetcher.get_default_branch(owner, repo)
            title = self._pr_title(state, issue_number)
            body = self._pr_body(state, issue_number)
            pr_url = fetcher.create_pull_request(
                owner, repo, title=title, head=branch, base=base, body=body
            )
        except RuntimeError as e:
            print(f"  PR API error: {e}")
            return {"pr_url": "", "pr_error": str(e), "pr_skip_reason": ""}

        print(f"  Pull request created: {pr_url}")
        return {"pr_url": pr_url, "pr_error": "", "pr_skip_reason": ""}

    def _execute(self, state: TaskState) -> dict:
        print("\n[PR Creator]")

        work_repo_path = state.get("work_repo_path", "")
        baseline_sha = state.get("repo_baseline_sha", "")
        platform = state.get("source_platform", "github")

        if not work_repo_path:
            reason = "No work repository path from executor."
            print(f"  Skipping PR: {reason}")
            return {"pr_url": "", "pr_error": "", "pr_skip_reason": reason}

        info = change_summary(work_repo_path, baseline_sha)
        print(
            f"  Git state: baseline={info['baseline_sha']}, "
            f"uncommitted={info['uncommitted']}, "
            f"commits_since_baseline={info['commits_since_baseline']}"
        )

        if not has_changes_since(work_repo_path, baseline_sha):
            reason = (
                "No changes detected since workflow baseline.\n"
                f"  uncommitted={info['uncommitted']}, "
                f"commits_since_baseline={info['commits_since_baseline']}, "
                f"baseline={info['baseline_sha']}"
            )
            print(f"  Skipping PR: {reason}")
            if info["diff_stat"] != "(no diff)":
                print(f"  Diff stat: {info['diff_stat']}")
            return {"pr_url": "", "pr_error": "", "pr_skip_reason": reason}

        target_repo = state.get("target_repo_path", "")
        github_issue_url = state.get("github_issue_url", "")
        jira_issue_url = state.get("jira_issue_url", "")
        bitbucket_issue_url = state.get("bitbucket_issue_url", "")

        if platform == "github":
            return self._create_github_pr(
                state, work_repo_path, baseline_sha, target_repo, github_issue_url
            )
        elif platform == "bitbucket":
            return self._create_bitbucket_pr(
                state, work_repo_path, baseline_sha, target_repo, bitbucket_issue_url, github_issue_url, jira_issue_url
            )
        elif platform == "jira":
            return self._create_bitbucket_pr(
                state, work_repo_path, baseline_sha, target_repo, bitbucket_issue_url, github_issue_url, jira_issue_url
            )
        else:
            return self._create_github_pr(
                state, work_repo_path, baseline_sha, target_repo, github_issue_url
            )

    def _resolve_owner_repo(self, state: TaskState, platform_github: bool = False) -> tuple[str, str]:
        """Resolve owner/repo from state URLs based on platform."""
        if platform_github:
            target_repo = state.get("target_repo_path", "")
            github_issue_url = state.get("github_issue_url", "")
            try:
                return GitHubFetcher.parse_repo_remote(target_repo, github_issue_url)
            except ValueError as e:
                raise PrCreationError(str(e)) from e
        else:
            target_repo = state.get("target_repo_path", "")
            bitbucket_issue_url = state.get("bitbucket_issue_url", "")
            if not bitbucket_issue_url:
                bitbucket_issue_url = state.get("jira_issue_url", "")
            try:
                return BitbucketFetcher.parse_repo_remote(target_repo, bitbucket_issue_url)
            except ValueError as e:
                raise PrCreationError(str(e)) from e

    def _issue_identifier(self, state: TaskState, platform: str) -> str | None:
        """Extract issue number/key from URLs for branch naming."""
        if platform == "github":
            return self._issue_number(state.get("github_issue_url", ""))
        elif platform == "jira":
            url = state.get("jira_issue_url", "") or state.get("bitbucket_issue_url", "")
            return self._jira_key_identifier(url)
        return self._issue_number(state.get("bitbucket_issue_url", ""))

    @staticmethod
    def _jira_key_identifier(jira_issue_url: str) -> str | None:
        if not jira_issue_url:
            return None
        match = re.search(r"/browse/([A-Za-z0-9]+(-[A-Za-z0-9]+)*)-(\d+)", jira_issue_url)
        if match:
            return f"{match.group(1)}-{match.group(3)}"
        return None

    def _create_github_pr(self, state: TaskState, work_repo_path: str, baseline_sha: str,
                          target_repo: str, github_issue_url: str) -> dict:
        token = os.getenv("GITHUB_TOKEN", "")
        if not token:
            raise PrCreationError("GITHUB_TOKEN is required to create a pull request")

        try:
            owner, repo = self._resolve_owner_repo(state, platform_github=True)
        except ValueError as e:
            return {"pr_url": "", "pr_error": str(e), "pr_skip_reason": ""}

        issue_number = self._issue_number(github_issue_url)
        branch = (
            f"agent/issue-{issue_number}"
            if issue_number
            else f"agent/run-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        )

        auth_remote = (
            f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
        )
        git_name = os.getenv("GIT_AUTHOR_NAME", "Agent Graph")
        git_email = os.getenv("GIT_AUTHOR_EMAIL", "agent@users.noreply.github.com")
        commit_msg = self._commit_message(state, issue_number)

        try:
            self._git(work_repo_path, "remote", "set-url", "origin", auth_remote)
            self._git(work_repo_path, "config", "user.name", git_name)
            self._git(work_repo_path, "config", "user.email", git_email)
            self._git(work_repo_path, "checkout", "-b", branch)
            if has_uncommitted_changes(work_repo_path):
                self._git(work_repo_path, "add", "-A")
                self._git(work_repo_path, "commit", "-m", commit_msg)
            elif commit_count_since(work_repo_path, baseline_sha) > 0:
                print(
                    f"  Using {commit_count_since(work_repo_path, baseline_sha)} "
                    "commit(s) already made by the agent"
                )
            self._git(work_repo_path, "push", "-u", "origin", branch)
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or str(e))
            print(f"  Git error: {err}")
            return {"pr_url": "", "pr_error": f"Git push failed: {err}", "pr_skip_reason": ""}

        fetcher = GitHubFetcher(token=token)
        try:
            base = fetcher.get_default_branch(owner, repo)
            title = self._pr_title(state, issue_number)
            body = self._pr_body(state, issue_number)
            pr_url = fetcher.create_pull_request(
                owner, repo, title=title, head=branch, base=base, body=body
            )
        except RuntimeError as e:
            print(f"  PR API error: {e}")
            return {"pr_url": "", "pr_error": str(e), "pr_skip_reason": ""}

        print(f"  Pull request created: {pr_url}")
        return {"pr_url": pr_url, "pr_error": "", "pr_skip_reason": ""}

    def _create_bitbucket_pr(self, state: TaskState, work_repo_path: str, baseline_sha: str,
                             target_repo: str, bitbucket_issue_url: str,
                             github_issue_url: str, jira_issue_url: str) -> dict:
        token = os.getenv("BITBUCKET_TOKEN", "")
        if not token:
            raise PrCreationError("BITBUCKET_TOKEN is required to create a pull request")

        try:
            owner, repo = self._resolve_owner_repo(state, platform_github=False)
        except ValueError as e:
            return {"pr_url": "", "pr_error": str(e), "pr_skip_reason": ""}

        identifier = self._issue_identifier(state, "bitbucket")
        branch = (
            f"agent/issue-{identifier}"
            if identifier
            else f"agent/run-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        )

        auth_remote = (
            f"https://x-token-auth:{token}@bitbucket.org/{owner}/{repo}.git"
        )
        git_name = os.getenv("GIT_AUTHOR_NAME", "Agent Graph")
        git_email = os.getenv("GIT_AUTHOR_EMAIL", "agent@users.noreply.github.com")
        commit_msg = self._commit_message(state, None)

        try:
            self._git(work_repo_path, "remote", "set-url", "origin", auth_remote)
            self._git(work_repo_path, "config", "user.name", git_name)
            self._git(work_repo_path, "config", "user.email", git_email)
            self._git(work_repo_path, "checkout", "-b", branch)
            if has_uncommitted_changes(work_repo_path):
                self._git(work_repo_path, "add", "-A")
                self._git(work_repo_path, "commit", "-m", commit_msg)
            elif commit_count_since(work_repo_path, baseline_sha) > 0:
                print(
                    f"  Using {commit_count_since(work_repo_path, baseline_sha)} "
                    "commit(s) already made by the agent"
                )
            self._git(work_repo_path, "push", "-u", "origin", branch)
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or str(e))
            print(f"  Git error: {err}")
            return {"pr_url": "", "pr_error": f"Git push failed: {err}", "pr_skip_reason": ""}

        fetcher = BitbucketFetcher(token=token)
        try:
            base = fetcher.get_default_branch(owner, repo)
            title = self._pr_body_brief(state)
            body = self._pr_body(state, None, platform="bitbucket")
            issue_id = None
            if identifier:
                match = re.search(r"-(\d+)$", identifier)
                if match:
                    issue_id = match.group(1)
            pr_url = fetcher.create_pull_request(
                owner, repo, title=title, head=branch, base=base, body=body
            )
            # Update PR with issue references via id comment if possible
            if issue_id:
                try:
                    self._git(work_repo_path, "-C", work_repo_path, "checkout", "-q", "-b", f"bb-linked-{issue_id}-pr")
                except (subprocess.CalledProcessError, Exception):
                    pass
        except RuntimeError as e:
            print(f"  PR API error: {e}")
            return {"pr_url": "", "pr_error": str(e), "pr_skip_reason": ""}

        print(f"  Pull request created: {pr_url}")
        return {"pr_url": pr_url, "pr_error": "", "pr_skip_reason": ""}

    def _pr_body_brief(self, state: TaskState) -> str:
        issue_line = state.get("issue", "").strip().split("\n")[0][:100]
        return issue_line or "Agent implementation"

    @staticmethod
    def _git(repo_path: str, *args: str) -> None:
        subprocess.run(
            ["git", "-C", repo_path, *args],
            check=True,
            capture_output=True,
        )

    @staticmethod
    def _issue_number(github_issue_url: str) -> int | None:
        if not github_issue_url:
            return None
        match = re.search(r"/issues/(\d+)", github_issue_url)
        return int(match.group(1)) if match else None

    @staticmethod
    def _commit_message(state: TaskState, issue_number: int | None) -> str:
        issue = state.get("issue", "").strip().split("\n")[0][:72]
        if issue_number:
            return f"Fix #{issue_number}: {issue}" if issue else f"Fix #{issue_number}"
        return issue or "Agent implementation"

    @staticmethod
    def _pr_title(state: TaskState, issue_number: int | None) -> str:
        issue_line = state.get("issue", "").strip().split("\n")[0][:80]
        if issue_number and issue_line:
            return f"[#{issue_number}] {issue_line}"
        if issue_number:
            return f"Agent changes for issue #{issue_number}"
        return issue_line or "Agent implementation"

    @staticmethod
    def _pr_body(state: TaskState, issue_number: int | None, platform: str = "github") -> str:
        parts = []
        if issue_number:
            parts.append(f"Fixes #{issue_number}\n")
        if state.get("plan"):
            parts.append(f"## Plan\n\n{state['plan']}\n")
        if state.get("implementation_result"):
            parts.append(f"## Implementation\n\n{state['implementation_result']}\n")
        if state.get("verification_result"):
            parts.append(f"## Verification\n\n{state['verification_result']}\n")
        if state.get("diff_patch"):
            max_diff_len = 5000 if platform == "bitbucket" else 8000
            parts.append(f"## Diff\n\n```diff\n{state['diff_patch'][:max_diff_len]}\n```\n")
        return "\n".join(parts)
