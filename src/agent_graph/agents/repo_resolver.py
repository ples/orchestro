"""Resolves initial target repositories from environment config."""

import os
import stat

from agent_graph.state import RepoRecord, TaskState

from .base import BaseAgent


def _is_git_url(url: str) -> bool:
    return (
        url.startswith("http://")
        or url.startswith("https://")
        or url.startswith("git@")
        or url.startswith("ssh://")
    )


def _is_valid_path(path: str) -> bool:
    if not path:
        return False
    try:
        resolved = os.path.abspath(path)
        return stat.S_ISDIR(os.stat(resolved).st_mode)
    except (OSError, ValueError):
        return False


def _collect_env_repos(platform: str) -> list[str]:
    """Collect repo URLs from env. Jira workflows skip TARGET_REPO_PATH (often a GitHub test default)."""
    repos: list[str] = []
    seen: set[str] = set()

    if platform == "jira":
        env_keys = ("JIRA_REPO_PATH", "BITBUCKET_REPO")
    elif platform == "bitbucket":
        env_keys = ("BITBUCKET_REPO", "JIRA_REPO_PATH", "TARGET_REPO_PATH")
    else:
        env_keys = ("TARGET_REPO_PATH",)

    for env_key in env_keys:
        val = os.getenv(env_key, "").strip()
        if val:
            for part in val.split(","):
                p = part.strip()
                if p and p not in seen:
                    repos.append(p)
                    seen.add(p)

    bb_owner = os.getenv("BB_DEFAULT_OWNER", "").strip()
    bb_repo = os.getenv("BB_DEFAULT_REPO", "").strip()
    if bb_owner and bb_repo:
        url = f"https://bitbucket.org/{bb_owner}/{bb_repo}.git"
        if url not in seen:
            repos.append(url)
            seen.add(url)

    if platform not in ("jira", "bitbucket"):
        gh_owner = os.getenv("GH_DEFAULT_OWNER", "").strip()
        gh_repo = os.getenv("GH_DEFAULT_REPO", "").strip()
        if gh_owner and gh_repo:
            url = f"https://github.com/{gh_owner}/{gh_repo}.git"
            if url not in seen:
                repos.append(url)
                seen.add(url)

    return repos


def _repo_from_url(url: str) -> RepoRecord | None:
    if not (url and (_is_git_url(url) or _is_valid_path(url))):
        return None
    return {"target_repo_path": url}


def _detect_from_issue(state: TaskState) -> list[RepoRecord]:
    from agent_graph.agents.repo_detector import RepoDetectorAgent

    issue_state = {**state, "target_repos": []}
    return RepoDetectorAgent().run(issue_state).get("target_repos", [])


class RepoResolverAgent(BaseAgent):
    """Resolves initial target repositories from environment."""

    name = "repo_resolver"

    def _execute(self, state: TaskState) -> dict:
        platform = state.get("source_platform", "github")

        if platform == "github":
            existing = state.get("target_repo_path", "").strip()
            record = _repo_from_url(existing)
            if record:
                print(f"  [Repo Resolver] GitHub: {record['target_repo_path']}")
            else:
                record = _repo_from_url(os.getenv("TARGET_REPO_PATH", ""))
                if record:
                    print(f"  [Repo Resolver] GitHub (env): {record['target_repo_path']}")
            repos = [record] if record else []
            if repos:
                return {"target_repos": repos}
            return {"target_repos": []}

        detected = _detect_from_issue(state)
        if detected:
            for r in detected:
                print(f"  [Repo Resolver] From issue: {r['target_repo_path']}")
            return {"target_repos": detected}

        env_repos = _collect_env_repos(platform)
        target_repos: list[RepoRecord] = []
        for url in env_repos:
            record = _repo_from_url(url)
            if record:
                target_repos.append(record)

        if target_repos:
            for r in target_repos:
                print(f"  [Repo Resolver] From env: {r['target_repo_path']}")
            return {"target_repos": target_repos}

        target_path = os.getenv("TARGET_REPO_PATH", "").strip()
        if platform == "jira" and target_path and "github.com" in target_path:
            print(
                "  [Repo Resolver] TARGET_REPO_PATH points to GitHub; "
                "set BB_DEFAULT_OWNER or JIRA_REPO_PATH for Bitbucket repos"
            )
        else:
            fallback = _repo_from_url(target_path)
            if fallback:
                print(f"  [Repo Resolver] Fallback: {fallback['target_repo_path']}")
                return {"target_repos": [fallback]}

        print(f"  [Repo Resolver] No repos found for {platform}")
        return {"target_repos": []}
