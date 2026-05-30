"""Deploy environment resolution and env.*.branch.* git tagging."""

from __future__ import annotations

import re
import subprocess
import time

VALID_DEPLOY_ENVS = frozenset(
    {"dev", "rc2", "dev1", "dev2", "dev3", "dev4", "dev5", "stage", "prod"}
)

_PARSE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)deploy-env:\s*(\w+)"),
    re.compile(r"(?i)deploy_env\s*=\s*(\w+)"),
    re.compile(r"(?i)(?:deploy|release)\s+(?:to|for)\s+(\w+)"),
    re.compile(r"(?i)\bdeploy\s+(\w+)\b"),
    re.compile(r"(?i)tag-env\s+ENV=(\w+)"),
    re.compile(r"(?im)^ENV=(\w+)\s*$"),
    re.compile(r"(?i)(?:tag|env)[- ](?:for|to)\s+(\w+)"),
]


def normalize_deploy_env(value: str | None) -> str | None:
    if not value or not str(value).strip():
        return None
    normalized = str(value).strip().lower()
    if normalized in VALID_DEPLOY_ENVS:
        return normalized
    return None


def build_env_tag_name(deploy_env: str, branch: str) -> str:
    return f"env.{deploy_env}.branch.{branch}"


def parse_deploy_env_from_text(text: str) -> str | None:
    if not text or not text.strip():
        return None
    for pattern in _PARSE_PATTERNS:
        match = pattern.search(text)
        if match:
            env = normalize_deploy_env(match.group(1))
            if env:
                return env
    return None


def resolve_deploy_env(
    *,
    cli: str | None = None,
    issue: str = "",
    input_prompt: str = "",
    env_var: str | None = None,
) -> str | None:
    """Resolve deploy env: CLI > DEPLOY_ENV env var > parse from issue + prompt."""
    from_cli = normalize_deploy_env(cli)
    if from_cli:
        return from_cli
    from_env = normalize_deploy_env(env_var)
    if from_env:
        return from_env
    combined = "\n\n".join(p for p in (issue, input_prompt) if p and p.strip())
    return parse_deploy_env_from_text(combined)


def resolve_deploy_env_with_source(
    *,
    cli: str | None = None,
    issue: str = "",
    input_prompt: str = "",
    env_var: str | None = None,
) -> tuple[str | None, str]:
    """Resolve deploy env and return (value, source)."""
    from_cli = normalize_deploy_env(cli)
    if from_cli:
        return from_cli, "cli"

    from_env = normalize_deploy_env(env_var)
    if from_env:
        return from_env, "env"

    combined = "\n\n".join(p for p in (issue, input_prompt) if p and p.strip())
    from_text = parse_deploy_env_from_text(combined)
    if from_text:
        return from_text, "prompt/issue"

    return None, ""


def _git(repo_path: str, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", repo_path, *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _list_local_env_tags(repo_path: str, deploy_env: str) -> list[str]:
    prefix = f"env.{deploy_env}.branch."
    result = _git(repo_path, "tag", "-l", f"{prefix}*", check=False)
    if result.returncode != 0:
        return []
    return [t.strip() for t in result.stdout.splitlines() if t.strip()]


def _list_remote_env_tags(repo_path: str, deploy_env: str) -> list[str]:
    prefix = f"env.{deploy_env}.branch."
    result = _git(
        repo_path,
        "ls-remote",
        "--tags",
        "origin",
        f"refs/tags/{prefix}*",
        check=False,
    )
    if result.returncode != 0:
        return []
    tags: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        ref = parts[1]
        if ref.startswith("refs/tags/"):
            tag = ref[len("refs/tags/") :]
            if tag.endswith("^{}"):
                continue
            tags.append(tag)
    return tags


def _delete_local_tags(repo_path: str, tags: list[str]) -> None:
    for tag in tags:
        _git(repo_path, "tag", "-d", tag, check=False)


def _delete_remote_tags(repo_path: str, tags: list[str]) -> None:
    for tag in tags:
        _git(repo_path, "push", "origin", "--delete", tag, check=False)


def _tag_message(deploy_env: str, branch: str) -> str:
    return f"Release to {deploy_env} from branch {branch}"


def _cleanup_env_tags(repo_path: str, deploy_env: str) -> None:
    local_tags = _list_local_env_tags(repo_path, deploy_env)
    _delete_local_tags(repo_path, local_tags)
    _git(repo_path, "fetch", "--tags", "--prune", check=False)
    remote_tags = _list_remote_env_tags(repo_path, deploy_env)
    _delete_remote_tags(repo_path, remote_tags)
    time.sleep(3)
    stale_local = _list_local_env_tags(repo_path, deploy_env)
    _delete_local_tags(repo_path, stale_local)


def prepare_deploy_env_tag(
    repo_path: str, branch: str, deploy_env: str
) -> tuple[str, str]:
    """Remove old env tags and create annotated tag at HEAD (local only, no push)."""
    env = normalize_deploy_env(deploy_env)
    if not env:
        return "", f"Invalid deploy environment: {deploy_env}"
    if not repo_path or not branch:
        return "", "Missing repo path or branch for deploy tag"

    tag_name = build_env_tag_name(env, branch)
    try:
        _cleanup_env_tags(repo_path, env)
        result = _git(
            repo_path,
            "tag",
            "-a",
            tag_name,
            "-m",
            _tag_message(env, branch),
            check=False,
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "git tag failed").strip()
            return "", f"Git tag failed: {err}"
        return tag_name, ""
    except OSError as e:
        return "", f"Deploy tag error: {e}"


def refresh_deploy_env_tag_at_head(
    repo_path: str, tag_name: str, deploy_env: str, branch: str
) -> str:
    """Move an existing deploy tag to current HEAD (e.g. after rebase). Returns error or ""."""
    env = normalize_deploy_env(deploy_env)
    if not env:
        return f"Invalid deploy environment: {deploy_env}"
    result = _git(
        repo_path,
        "tag",
        "-f",
        "-a",
        tag_name,
        "-m",
        _tag_message(env, branch),
        check=False,
    )
    if result.returncode != 0:
        return (result.stderr or result.stdout or "git tag -f failed").strip()
    return ""


def push_deploy_env_tag(repo_path: str, tag_name: str) -> str:
    """Push deploy tag only. Prefer pushing tag with branch via _push_branch."""
    if not tag_name:
        return ""
    result = _git(repo_path, "push", "origin", tag_name, check=False)
    if result.returncode != 0:
        return (result.stderr or result.stdout or "git push tag failed").strip()
    return ""


def tag_deploy_env(repo_path: str, branch: str, deploy_env: str) -> tuple[str, str]:
    """Prepare and push env tag (tag-only push; used in tests)."""
    tag_name, err = prepare_deploy_env_tag(repo_path, branch, deploy_env)
    if err:
        return "", err
    push_err = push_deploy_env_tag(repo_path, tag_name)
    if push_err:
        return "", f"Git push tag failed: {push_err}"
    return tag_name, ""
