"""Shared git clone helpers for planner and executor."""

from __future__ import annotations

import os
import subprocess
from urllib.parse import quote, urlparse

from agent_graph.exceptions import ExecutorError
from agent_graph.git_utils import rev_parse
from agent_graph.logging_config import step, verbose_print

DEFAULT_CLONE_TIMEOUT = 120
BITBUCKET_GIT_USERNAME = "x-bitbucket-api-token-auth"


def clone_timeout_seconds() -> int:
    raw = os.getenv("GIT_CLONE_TIMEOUT", "").strip()
    if raw.isdigit():
        return int(raw)
    return DEFAULT_CLONE_TIMEOUT


def _bitbucket_username() -> str:
    return (
        os.getenv("BB_USERNAME", "").strip()
        or os.getenv("ATLASSIAN_EMAIL", "").strip()
        or os.getenv("JIRA_EMAIL", "").strip()
    )


def _bitbucket_token() -> str:
    return os.getenv("BITBUCKET_TOKEN", "").strip()


def bitbucket_api_auth() -> tuple[str, str] | None:
    """HTTP Basic auth for Bitbucket REST API (API tokens / app passwords)."""
    token = _bitbucket_token()
    user = _bitbucket_username()
    if user and token:
        return (user, token)
    return None


def bitbucket_api_headers() -> dict[str, str]:
    """Request headers for Bitbucket REST API.

    API tokens use Basic auth (see bitbucket_api_auth). Repository/workspace
    access tokens use Bearer when no username/email is configured.
    """
    headers = {"Accept": "application/json"}
    if _bitbucket_token() and not _bitbucket_username():
        headers["Authorization"] = f"Bearer {_bitbucket_token()}"
    return headers


def bitbucket_request_kwargs(*, timeout: int = 30) -> dict:
    """Shared requests kwargs for Bitbucket REST calls."""
    kwargs: dict = {"headers": bitbucket_api_headers(), "timeout": timeout}
    auth = bitbucket_api_auth()
    if auth:
        kwargs["auth"] = auth
    return kwargs


def authenticated_clone_url(repo_url: str) -> str:
    """Return a clone URL with credentials embedded when available."""
    if "@" in repo_url:
        return repo_url

    if repo_url.startswith("https://github.com/"):
        token = os.getenv("GITHUB_TOKEN", "")
        if token:
            return repo_url.replace(
                "https://github.com/",
                f"https://x-access-token:{token}@github.com/",
                1,
            )
        return repo_url

    if "bitbucket.org" in repo_url:
        token = _bitbucket_token()
        if token:
            parsed = urlparse(repo_url)
            host = parsed.netloc or "bitbucket.org"
            path = parsed.path or ""
            return (
                f"https://{BITBUCKET_GIT_USERNAME}:{quote(token, safe='')}"
                f"@{host}{path}"
            )
        return repo_url

    return repo_url


def _is_remote(url: str) -> bool:
    return (
        url.startswith("http://")
        or url.startswith("https://")
        or url.startswith("git@")
    )


def _repo_name_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1].replace(".git", "")


def _truncate_stderr(stderr: str, limit: int = 500) -> str:
    text = stderr.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _finalize_clone(repo_path: str) -> str:
    subprocess.run(
        ["git", "-C", repo_path, "add", "."],
        check=False,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            repo_path,
            "commit",
            "-m",
            "initial",
            "--allow-empty-message",
        ],
        check=False,
        capture_output=True,
    )
    return rev_parse(repo_path)


def _clone_into_path(repo_path: str, url: str, *, timeout_sec: int) -> None:
    if _is_remote(url):
        clone_url = authenticated_clone_url(url)
        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", clone_url, repo_path],
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
        except subprocess.TimeoutExpired as exc:
            name = os.path.basename(repo_path)
            msg = f"git clone timed out after {timeout_sec}s for {name}"
            verbose_print(f"  [Clone] {msg}")
            raise ExecutorError(msg) from exc
        except subprocess.CalledProcessError as exc:
            name = os.path.basename(repo_path)
            err = _truncate_stderr(exc.stderr or "")
            msg = f"git clone failed for {name}: {err or exc}"
            verbose_print(f"  [Clone] {msg}")
            raise ExecutorError(msg) from exc
        else:
            if result.stderr:
                verbose_print(f"  [Clone] {result.stderr.strip()[:200]}")
        return

    if not os.path.isdir(url):
        raise ExecutorError(f"Target repo not found: {url}")
    step(f"  [Clone] copy {os.path.basename(repo_path)}")
    os.makedirs(os.path.dirname(repo_path), exist_ok=True)
    subprocess.run(
        ["cp", "-R", f"{url}/.", repo_path],
        check=True,
        capture_output=True,
    )


def clone_planner_workspace(urls: list[str]) -> tuple[str, dict[str, tuple[str, str]]]:
    """Clone multiple repositories under one parent directory.

    Returns:
        (parent_dir, {repo_url: (repo_path, baseline_sha)})
    """
    valid_urls = [u.strip() for u in urls if u and u.strip()]
    if not valid_urls:
        return "", {}

    parent = subprocess.check_output(
        ["mktemp", "-d", "-t", "planner_workspace_"], text=True
    ).strip()
    timeout_sec = clone_timeout_seconds()
    results: dict[str, tuple[str, str]] = {}

    for i, url in enumerate(valid_urls):
        repo_name = _repo_name_from_url(url)
        repo_path = os.path.join(parent, repo_name)
        step(f"  [Clone] ({i + 1}/{len(valid_urls)}) {repo_name}")
        _clone_into_path(repo_path, url, timeout_sec=timeout_sec)
        baseline = _finalize_clone(repo_path)
        results[url] = (repo_path, baseline)
        verbose_print(f"  [Clone] ready: {repo_path}")

    step(f"  [Clone] workspace ready ({len(results)} repos)")
    return parent, results


def clone_repository(
    url: str,
    *,
    prefix: str = "agent_repo_",
    timeout: int | None = None,
) -> tuple[str, str]:
    """Clone or copy a repository into a temp directory.

    Returns:
        (repo_path, baseline_sha) where baseline_sha is HEAD after initial commit.
    """
    if not url or not url.strip():
        return "", ""

    timeout_sec = timeout if timeout is not None else clone_timeout_seconds()
    tmp_dir = subprocess.check_output(
        ["mktemp", "-d", "-t", prefix], text=True
    ).strip()

    repo_name = _repo_name_from_url(url) if _is_remote(url) else os.path.basename(
        os.path.abspath(url)
    )
    repo_path = os.path.join(tmp_dir, repo_name)
    if _is_remote(url):
        step(f"  [Clone] {repo_name}")
    _clone_into_path(repo_path, url, timeout_sec=timeout_sec)
    verbose_print(f"  [Clone] cloned into {repo_path}")
    baseline = _finalize_clone(repo_path)
    return repo_path, baseline


def reset_repo_to_baseline(repo_path: str, baseline_sha: str) -> None:
    """Discard working tree and index changes back to baseline."""
    if not repo_path or not baseline_sha:
        return
    subprocess.run(
        ["git", "-C", repo_path, "reset", "--hard", baseline_sha],
        check=False,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", repo_path, "clean", "-fd"],
        check=False,
        capture_output=True,
    )
