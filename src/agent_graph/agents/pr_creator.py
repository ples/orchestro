"""Creates a GitHub pull request from the executor worktree."""

import os
import re
import subprocess
from datetime import datetime, timezone

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
    def _pr_body(state: TaskState, issue_number: int | None) -> str:
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
            parts.append(f"## Diff\n\n```diff\n{state['diff_patch'][:8000]}\n```\n")
        return "\n".join(parts)
