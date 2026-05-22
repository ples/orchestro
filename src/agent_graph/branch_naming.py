"""Branch naming conventions for agent-created git branches."""

from __future__ import annotations

import re

_HOTFIX_TYPES = frozenset(
    {
        "bug",
        "defect",
        "incident",
        "hotfix",
        "problem",
        "support",
        "sub-bug",
    }
)
_FEATURE_TYPES = frozenset(
    {
        "story",
        "epic",
        "feature",
        "new feature",
        "improvement",
        "enhancement",
        "task",
        "initiative",
    }
)
_BRANCH_PREFIX_RE = re.compile(r"^(hotfix|feature)/[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_issue_type_from_text(issue: str) -> str | None:
    """Read ``Type: Bug`` style line from formatted Jira issue text."""
    match = re.search(r"(?im)^type:\s*(.+?)\s*$", issue or "")
    return match.group(1).strip() if match else None


def branch_prefix(issue_type: str | None, issue_text: str = "") -> str:
    """Return ``hotfix`` or ``feature`` based on ticket type and scope."""
    normalized = (issue_type or "").strip().lower()
    if normalized in _HOTFIX_TYPES:
        return "hotfix"
    if normalized in _FEATURE_TYPES:
        return "feature"

    combined = f"{issue_type or ''} {issue_text}".lower()
    if re.search(r"\b(bug|defect|fix|hotfix|regression|patch)\b", combined):
        return "hotfix"
    if re.search(r"\b(story|epic|feature|implement|add support)\b", combined):
        return "feature"
    return "hotfix"


def branch_context_slug(issue_text: str, *, max_words: int = 3) -> str:
    """Short kebab-case context from the ticket title (at most *max_words* words)."""
    title = (issue_text or "").split("\n", 1)[0].strip()
    title = re.sub(r"^[A-Z][A-Z0-9]+-\d+:\s*", "", title)
    title = re.sub(r"^#\d+:\s*", "", title)
    words = re.findall(r"[a-z0-9]+", title.lower())
    skip = {
        "fix",
        "add",
        "update",
        "the",
        "a",
        "an",
        "for",
        "in",
        "on",
        "to",
        "and",
        "or",
        "of",
        "is",
        "be",
    }
    meaningful = [w for w in words if w not in skip and len(w) > 1]
    if not meaningful:
        meaningful = words
    slug_words = meaningful[:max_words] or ["change"]
    return "-".join(slug_words)


def build_branch_name(issue: str, issue_type: str | None = None) -> str:
    """Build branch name like ``hotfix/email-verified-fix``."""
    parsed_type = issue_type or parse_issue_type_from_text(issue)
    prefix = branch_prefix(parsed_type, issue)
    slug = branch_context_slug(issue)
    return f"{prefix}/{slug}"


def branch_creation_instruction(issue: str, issue_type: str | None = None) -> str:
    """Single instruction line for OpenHands execution prompts."""
    branch = build_branch_name(issue, issue_type)
    parsed_type = issue_type or parse_issue_type_from_text(issue)
    prefix = branch_prefix(parsed_type, issue)
    other = "feature" if prefix == "hotfix" else "hotfix"
    return (
        f"Before editing files, create and check out branch `{branch}` "
        f"(`git checkout -b {branch}`). "
        f"Use `{prefix}/` for bugs and small fixes; use `{other}/` for new functionality. "
        f"The part after the slash must be at most three hyphen-separated words "
        f"describing the change (example: `hotfix/email-verified-fix`)."
    )


def is_agent_branch_name(name: str) -> bool:
    return bool(_BRANCH_PREFIX_RE.match((name or "").strip()))


def current_branch(repo_path: str) -> str | None:
    import subprocess

    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    branch = result.stdout.strip()
    if branch == "HEAD":
        return None
    return branch


def resolve_work_branch(repo_path: str, issue: str, issue_type: str | None = None) -> str:
    """Prefer an existing hotfix/feature branch the agent already created."""
    current = current_branch(repo_path)
    if current and is_agent_branch_name(current):
        return current
    return build_branch_name(issue, issue_type)

