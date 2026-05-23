"""Branch naming conventions for agent-created git branches."""

from __future__ import annotations

import re
import subprocess

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


def ticket_key_slug(issue: str) -> str | None:
    """Jira-style key from the issue title, e.g. ``minsky-14576``."""
    title = (issue or "").split("\n", 1)[0]
    match = re.search(r"\b([A-Z][A-Z0-9]+-\d+)\b", title)
    return match.group(1).lower() if match else None


def branch_exists_local(repo_path: str, branch: str) -> bool:
    result = subprocess.run(
        ["git", "-C", repo_path, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True,
    )
    return result.returncode == 0


def list_local_agent_branches(repo_path: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", repo_path, "branch", "--list", "hotfix/*", "feature/*"],
        capture_output=True,
        text=True,
    )
    branches: list[str] = []
    for line in result.stdout.splitlines():
        name = line.strip().lstrip("* ").strip()
        if is_agent_branch_name(name):
            branches.append(name)
    return branches


def branch_name_candidates(preferred: str, issue: str = "") -> list[str]:
    """Ordered branch names to try when *preferred* is unavailable."""
    seen: set[str] = set()
    candidates: list[str] = []

    def add(name: str) -> None:
        if name in seen or not is_agent_branch_name(name):
            return
        seen.add(name)
        candidates.append(name)

    add(preferred)
    key = ticket_key_slug(issue)
    if key:
        add(f"{preferred}-{key}")
    for n in range(2, 21):
        add(f"{preferred}-{n}")
    return candidates


def resolve_work_branch(repo_path: str, issue: str, issue_type: str | None = None) -> str:
    """Prefer an existing hotfix/feature branch the agent already created."""
    current = current_branch(repo_path)
    if current and is_agent_branch_name(current):
        return current

    preferred = build_branch_name(issue, issue_type)
    if branch_exists_local(repo_path, preferred):
        return preferred

    existing = list_local_agent_branches(repo_path)
    prefix = preferred.split("/", 1)[0] + "/"
    prefix_matches = [b for b in existing if b.startswith(prefix)]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    if len(existing) == 1:
        return existing[0]

    return preferred


def checkout_work_branch(
    repo_path: str,
    preferred: str,
    *,
    issue: str = "",
) -> str:
    """Check out a work branch, reusing an existing one or trying suffixed names."""
    current = current_branch(repo_path)
    for candidate in branch_name_candidates(preferred, issue):
        if current == candidate:
            return candidate
        if branch_exists_local(repo_path, candidate):
            _git(repo_path, "checkout", candidate)
            return candidate
        try:
            _git(repo_path, "checkout", "-b", candidate)
            return candidate
        except subprocess.CalledProcessError as exc:
            err = _stderr(exc)
            if "already exists" in err.lower():
                continue
            raise
    return preferred


def _git(repo_path: str, *args: str) -> None:
    subprocess.run(
        ["git", "-C", repo_path, *args],
        check=True,
        capture_output=True,
    )


def _stderr(exc: subprocess.CalledProcessError) -> str:
    raw = exc.stderr
    if isinstance(raw, bytes):
        return raw.decode(errors="replace")
    return raw or str(exc)

