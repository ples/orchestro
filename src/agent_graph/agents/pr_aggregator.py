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
from agent_graph.deploy_env import prepare_deploy_env_tag, refresh_deploy_env_tag_at_head
from agent_graph.pr_skip import (
    NO_CHANGES_SKIP,
    REQUIRED_CHANGES_MISSING,
    format_no_changes_skip,
    is_no_changes_summary,
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
        all_skips: list[str] = []
        all_deploy_tags: list[str] = []
        all_deploy_tag_errors: list[str] = []
        all_push_modes: list[str] = []
        updated_repos: list[RepoRecord] = []

        print(f"\n[PR Aggregator] Creating PRs for {len(target_repos)} repository(ies)...")

        for i, repo in enumerate(target_repos):
            repo_out = dict(repo)
            target = repo.get("target_repo_path", "?")
            skip_reason = repo.get("pr_skip_reason", "")
            pr_err = repo.get("pr_error", "")

            if skip_reason == REQUIRED_CHANGES_MISSING:
                msg = pr_err or f"Required changes missing in {target}"
                repo_out["pr_error"] = msg
                repo_out["pr_skip_reason"] = ""
                all_errors.append(f"{target}: {msg}")
                updated_repos.append(repo_out)
                continue
            if pr_err and "required changes missing" in pr_err.lower():
                all_errors.append(f"{target}: {pr_err}")
                updated_repos.append(repo_out)
                continue
            if skip_reason == NO_CHANGES_SKIP or is_no_changes_summary(skip_reason):
                repo_out["pr_skip_reason"] = NO_CHANGES_SKIP
                repo_out["pr_error"] = ""
                all_skips.append(format_no_changes_skip(target))
                updated_repos.append(repo_out)
                continue
            if pr_err and is_no_changes_summary(pr_err):
                repo_out["pr_skip_reason"] = NO_CHANGES_SKIP
                repo_out["pr_error"] = ""
                all_skips.append(format_no_changes_skip(target))
                updated_repos.append(repo_out)
                continue
            if pr_err:
                all_errors.append(f"{target}: {pr_err}")
                updated_repos.append(repo_out)
                continue

            print(f"  [Repo {i+1}/{len(target_repos)}] Target: {target}")

            pr_result = self._create_pr(state, repo)
            pr_url = pr_result.get("pr_url", "")
            if pr_url:
                repo_out["pr_url"] = pr_url
                all_pull_urls.append(pr_url)
            pr_err = pr_result.get("pr_error", "")
            result_skip = pr_result.get("pr_skip_reason", "")
            if result_skip == NO_CHANGES_SKIP:
                repo_out["pr_skip_reason"] = NO_CHANGES_SKIP
                repo_out["pr_error"] = ""
                all_skips.append(format_no_changes_skip(target))
            elif pr_err:
                repo_out["pr_error"] = pr_err
                all_errors.append(f"{target}: {pr_err}")
            tag_name = pr_result.get("deploy_tag_name", "")
            tag_err = pr_result.get("deploy_tag_error", "")
            push_mode = pr_result.get("pr_push_mode", "")
            pr_branch = pr_result.get("pr_branch", "")
            if pr_branch:
                repo_out["pr_branch"] = pr_branch
            if tag_name:
                repo_out["deploy_tag_name"] = tag_name
                all_deploy_tags.append(f"{target}: {tag_name}")
            if tag_err:
                repo_out["deploy_tag_error"] = tag_err
                all_deploy_tag_errors.append(f"{target}: {tag_err}")
            if push_mode:
                repo_out["pr_push_mode"] = push_mode
                all_push_modes.append(f"{target}: {push_mode}")
            updated_repos.append(repo_out)

        if all_pull_urls:
            print(f"  [PR Aggregator] Created {len(all_pull_urls)} pull request(s).")
        if all_skips:
            print(f"  [PR Aggregator] Skipped {len(all_skips)} repo(s) (no changes).")
        if all_errors:
            print(f"  [PR Aggregator] {len(all_errors)} repo(s) had PR errors.")
        if all_deploy_tags:
            print(f"  [PR Aggregator] Pushed {len(all_deploy_tags)} deploy tag(s).")
        if all_deploy_tag_errors:
            print(f"  [PR Aggregator] {len(all_deploy_tag_errors)} deploy tag error(s).")

        return {
            "target_repos": updated_repos,
            "pr_url": "\n".join(all_pull_urls),
            "pr_error": "\n".join(all_errors) if all_errors else "",
            "pr_skip_reason": "\n".join(all_skips) if all_skips else "",
            "deploy_tag_name": "\n".join(all_deploy_tags),
            "deploy_tag_error": "\n".join(all_deploy_tag_errors),
            "pr_push_mode": "\n".join(all_push_modes),
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

        if "bitbucket.org" in target:
            return self._make_bitbucket_pr(state, repo, issue_text)

        if "github.com" in target:
            return self._make_github_pr(state, repo, issue_text, issue_number, target)

        platform = state.get("source_platform", "github")
        if platform in ("jira", "bitbucket"):
            return self._make_bitbucket_pr(state, repo, issue_text)

        return self._make_github_pr(state, repo, issue_text, issue_number, target)

    # -- GitHub PR -----------------------------------------------------------

    def _make_github_pr(self, state: TaskState, repo: RepoRecord,
                        issue_text: str, issue_number: int | None,
                        target: str) -> dict:
        work_repo = repo.get("work_repo_path", "")
        baseline = repo.get("repo_baseline_sha", "")
        token = os.getenv("GITHUB_TOKEN", "")
        if not token:
            return {"pr_url": "", "pr_error": "GITHUB_TOKEN required"}

        owner, repo_name = self._resolve_owner_repo(state, repo=repo, platform_github=True)
        if not owner:
            return {"pr_url": "", "pr_error": "Could not resolve GitHub owner/repo"}

        branch = repo.get("pr_branch") or self._branch_name(
            state, issue_number, work_repo=work_repo
        )
        auth_remote = f"https://x-access-token:{token}@github.com/{owner}/{repo_name}.git"
        git_name = os.getenv("GIT_AUTHOR_NAME", "Agent Graph")
        git_email = os.getenv("GIT_AUTHOR_EMAIL", "agent@users.noreply.github.com")
        commit_msg = f"Fix #{issue_number}: {issue_text}" if issue_number else issue_text or "Agent implementation"
        tag_fields: dict[str, str] = {}

        try:
            self._git(work_repo, "remote", "set-url", "origin", auth_remote)
            self._git(work_repo, "config", "user.name", git_name)
            self._git(work_repo, "config", "user.email", git_email)
            branch = self._checkout_branch(work_repo, branch, issue=state.get("issue", ""))
            if has_uncommitted_changes(work_repo):
                self._git(work_repo, "add", "-A")
                self._git(work_repo, "commit", "-m", commit_msg)
            elif commit_count_since(work_repo, baseline) > 0:
                pass  # using existing commits
            tag_fields = self._prepare_deploy_env_tag(state, work_repo, branch)
            if tag_fields.get("deploy_tag_error"):
                return {"pr_url": "", "pr_error": tag_fields["deploy_tag_error"], **tag_fields}
            push_mode = self._push_branch(
                work_repo,
                branch,
                deploy_tag_name=tag_fields.get("deploy_tag_name") or None,
                deploy_env=state.get("deploy_env", ""),
            )
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e)
            fetcher = GitHubFetcher(token=token)
            existing = fetcher.find_pull_request_for_head(owner, repo_name, branch, state="all")
            if existing:
                print(f"  Push failed but existing PR found: {existing}")
                return {"pr_url": existing, "pr_error": "", **tag_fields}
            return {"pr_url": "", "pr_error": f"Git push failed: {err}", **tag_fields}

        fetcher = GitHubFetcher(token=token)
        try:
            base = fetcher.get_default_branch(owner, repo_name)
            title = self._gh_title(issue_text, issue_number)
            body = self._gh_body(state, repo, issue_number)
            pr_url = fetcher.create_pull_request(
                owner, repo_name, title=title, head=branch, base=base, body=body
            )
        except RuntimeError as e:
            existing = fetcher.find_pull_request_for_head(owner, repo_name, branch, state="all")
            if existing:
                return {"pr_url": existing, "pr_error": "", **tag_fields}
            return {"pr_url": "", "pr_error": f"PR API error: {e}", **tag_fields}

        return {
            "pr_url": pr_url,
            "pr_error": "",
            "pr_branch": branch,
            "pr_push_mode": push_mode,
            **tag_fields,
        }

    # -- Bitbucket PR --------------------------------------------------------

    def _make_bitbucket_pr(self, state: TaskState, repo: RepoRecord,
                           issue_text: str) -> dict:
        work_repo = repo.get("work_repo_path", "")
        baseline = repo.get("repo_baseline_sha", "")
        token = os.getenv("BITBUCKET_TOKEN", "")
        if not token:
            return {"pr_url": "", "pr_error": "BITBUCKET_TOKEN required"}

        owner, repo_name = self._resolve_owner_repo(state, repo=repo, platform_github=False)
        if not owner:
            return {"pr_url": "", "pr_error": "Could not resolve Bitbucket owner/repo"}

        branch = repo.get("pr_branch") or self._branch_name(
            state, None, work_repo=work_repo
        )
        from agent_graph.repo_clone import BITBUCKET_GIT_USERNAME

        auth_remote = (
            f"https://{BITBUCKET_GIT_USERNAME}:{token}@"
            f"bitbucket.org/{owner}/{repo_name}.git"
        )
        git_name = os.getenv("GIT_AUTHOR_NAME", "Agent Graph")
        git_email = os.getenv("GIT_AUTHOR_EMAIL", "agent@users.noreply.github.com")
        commit_msg = issue_text or "Agent implementation"
        tag_fields: dict[str, str] = {}

        try:
            self._git(work_repo, "remote", "set-url", "origin", auth_remote)
            self._git(work_repo, "config", "user.name", git_name)
            self._git(work_repo, "config", "user.email", git_email)
            branch = self._checkout_branch(work_repo, branch, issue=state.get("issue", ""))
            if has_uncommitted_changes(work_repo):
                self._git(work_repo, "add", "-A")
                self._git(work_repo, "commit", "-m", commit_msg)
            elif commit_count_since(work_repo, baseline) > 0:
                pass
            tag_fields = self._prepare_deploy_env_tag(state, work_repo, branch)
            if tag_fields.get("deploy_tag_error"):
                return {"pr_url": "", "pr_error": tag_fields["deploy_tag_error"], **tag_fields}
            push_mode = self._push_branch(
                work_repo,
                branch,
                deploy_tag_name=tag_fields.get("deploy_tag_name") or None,
                deploy_env=state.get("deploy_env", ""),
            )
        except subprocess.CalledProcessError as e:
            err = e.stderr.decode() if isinstance(e.stderr, bytes) else str(e)
            return {"pr_url": "", "pr_error": f"Git push failed: {err}", **tag_fields}

        fetcher = BitbucketFetcher(token=token)
        try:
            base = fetcher.get_default_branch(owner, repo_name)
            title = issue_text[:100] or "Agent changes"
            body = self._bb_body(state, repo)
            pr_url = fetcher.create_pull_request(
                owner, repo_name, title=title, head=branch, base=base, body=body
            )
        except RuntimeError as e:
            return {"pr_url": "", "pr_error": f"PR API error: {e}", **tag_fields}

        return {
            "pr_url": pr_url,
            "pr_error": "",
            "pr_branch": branch,
            "pr_push_mode": push_mode,
            **tag_fields,
        }

    # -- Shared helpers ------------------------------------------------------

    @staticmethod
    def _prepare_deploy_env_tag(
        state: TaskState, work_repo: str, branch: str
    ) -> dict[str, str]:
        deploy_env = state.get("deploy_env", "")
        if not deploy_env:
            return {}
        tag_name, tag_err = prepare_deploy_env_tag(work_repo, branch, deploy_env)
        if tag_name:
            print(f"  Deploy tag prepared: {tag_name} (will push with branch)")
        elif tag_err:
            print(f"  Deploy tag failed: {tag_err}")
        return {"deploy_tag_name": tag_name, "deploy_tag_error": tag_err}

    def _resolve_owner_repo(
        self,
        state: TaskState,
        *,
        repo: RepoRecord | None = None,
        platform_github: bool = True,
    ) -> tuple[str, str]:
        """Resolve owner/repo from the given repo record or legacy state fields."""
        if repo is not None:
            target = repo.get("target_repo_path", "")
            url = (
                repo.get("bitbucket_issue_url", "")
                or repo.get("github_issue_url", "")
                or repo.get("jira_issue_url", "")
                or state.get("jira_issue_url", "")
                or state.get("github_issue_url", "")
            )
            if platform_github:
                try:
                    return GitHubFetcher.parse_repo_remote(
                        target, url or state.get("github_issue_url", "")
                    )
                except ValueError:
                    return ("", "")
            try:
                return BitbucketFetcher.parse_repo_remote(target, url)
            except ValueError:
                return ("", "")

        target_repos = state.get("target_repos", [])
        if target_repos:
            first = target_repos[0]
            return self._resolve_owner_repo(state, repo=first, platform_github=platform_github)

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
    def _branch_name(
        state: TaskState,
        issue_number: int | None,
        *,
        work_repo: str = "",
    ) -> str:
        from agent_graph.branch_naming import resolve_work_branch

        issue = state.get("issue", "") or ""
        if work_repo:
            return resolve_work_branch(work_repo, issue)
        from agent_graph.branch_naming import build_branch_name

        return build_branch_name(issue)

    @staticmethod
    def _git(repo_path: str, *args: str) -> None:
        subprocess.run(["git", "-C", repo_path, *args], check=True, capture_output=True)

    def _checkout_branch(
        self, work_repo: str, branch: str, *, issue: str = ""
    ) -> str:
        from agent_graph.branch_naming import checkout_work_branch

        checked_out = checkout_work_branch(work_repo, branch, issue=issue)
        if checked_out != branch:
            print(f"  Using branch `{checked_out}` (preferred `{branch}` was unavailable)")
        return checked_out

    def _push_branch(
        self,
        work_repo: str,
        branch: str,
        *,
        deploy_tag_name: str | None = None,
        deploy_env: str = "",
    ) -> str:
        allow_hard_force = (
            os.getenv("PR_ALLOW_HARD_FORCE_PUSH", "1").strip().lower()
            in ("1", "true", "yes")
        )

        def _push_refs(*extra_args: str) -> None:
            refs: list[str] = []
            if deploy_tag_name:
                refs.append(deploy_tag_name)
            refs.append(branch)
            self._git(work_repo, "push", *extra_args, "-u", "origin", *refs)

        def _refresh_tag_after_rebase() -> None:
            if not deploy_tag_name or not deploy_env:
                return
            err = refresh_deploy_env_tag_at_head(
                work_repo, deploy_tag_name, deploy_env, branch
            )
            if err:
                raise subprocess.CalledProcessError(
                    1, ["git", "tag", "-f"], None, err.encode()
                )

        try:
            _push_refs()
            print(f"  Push mode: normal ({branch})")
            return "normal"
        except subprocess.CalledProcessError as first_err:
            err = (
                first_err.stderr.decode()
                if isinstance(first_err.stderr, bytes)
                else str(first_err.stderr or first_err)
            )
            if "rejected" not in err.lower() and "fetch first" not in err.lower():
                raise
        self._git(work_repo, "fetch", "origin", branch)
        try:
            self._git(work_repo, "rebase", f"origin/{branch}")
            _refresh_tag_after_rebase()
            _push_refs()
            print(f"  Push mode: rebased ({branch})")
            return "rebased"
        except subprocess.CalledProcessError:
            print(f"  Rebase onto origin/{branch} failed; pushing with --force-with-lease")
        self._git(work_repo, "fetch", "origin", branch)
        try:
            _refresh_tag_after_rebase()
            _push_refs("--force-with-lease")
            print(f"  Push mode: force-with-lease ({branch})")
            return "force-with-lease"
        except subprocess.CalledProcessError:
            if not allow_hard_force:
                raise subprocess.CalledProcessError(
                    1,
                    ["git", "push", "--force"],
                    None,
                    (
                        f"Force push blocked for {branch}; set PR_ALLOW_HARD_FORCE_PUSH=1 "
                        "to allow hard-force fallback."
                    ).encode(),
                )
            print(f"  Force-with-lease push failed; using --force for {branch}")
            _refresh_tag_after_rebase()
            _push_refs("--force")
            print(f"  Push mode: force ({branch})")
            return "force"

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
        branch = self._branch_name(state, issue_number, work_repo=work_repo)
        auth_remote = f"https://x-access-token:{token}@github.com/{owner}/{repo_name}.git"
        try:
            self._git(work_repo, "remote", "set-url", "origin", auth_remote)
            branch = self._checkout_branch(work_repo, branch, issue=state.get("issue", ""))
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
        branch = self._branch_name(state, None, work_repo=work_repo)
        from agent_graph.repo_clone import BITBUCKET_GIT_USERNAME

        auth_remote = (
            f"https://{BITBUCKET_GIT_USERNAME}:{token}@"
            f"bitbucket.org/{owner}/{repo_name}.git"
        )
        try:
            self._git(work_repo, "remote", "set-url", "origin", auth_remote)
            branch = self._checkout_branch(work_repo, branch, issue=state.get("issue", ""))
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
