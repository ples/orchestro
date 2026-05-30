"""Git helpers for detecting agent changes since workflow baseline."""

import subprocess


def rev_parse(repo_path: str, ref: str = "HEAD") -> str:
    result = subprocess.run(
        ["git", "-C", repo_path, "rev-parse", ref],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def capture_diff_since(repo_path: str, baseline_sha: str) -> str:
    if not repo_path:
        return ""
    args = ["git", "-C", repo_path, "diff"]
    if baseline_sha:
        args.append(baseline_sha)
    result = subprocess.run(args, capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


def diff_stat_since(repo_path: str, baseline_sha: str) -> str:
    if not repo_path:
        return ""
    args = ["git", "-C", repo_path, "diff", "--stat"]
    if baseline_sha:
        args.append(baseline_sha)
    result = subprocess.run(args, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def commit_count_since(repo_path: str, baseline_sha: str) -> int:
    if not repo_path or not baseline_sha:
        return 0
    result = subprocess.run(
        ["git", "-C", repo_path, "rev-list", "--count", f"{baseline_sha}..HEAD"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return 0
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


def has_uncommitted_changes(repo_path: str) -> bool:
    result = subprocess.run(
        ["git", "-C", repo_path, "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def has_changes_since(repo_path: str, baseline_sha: str) -> bool:
    if not repo_path:
        return False
    if has_uncommitted_changes(repo_path):
        return True
    if baseline_sha and commit_count_since(repo_path, baseline_sha) > 0:
        return True
    if baseline_sha:
        check = subprocess.run(
            ["git", "-C", repo_path, "diff", "--quiet", baseline_sha],
            capture_output=True,
        )
        return check.returncode == 1
    return bool(capture_diff_since(repo_path, "").strip())


def apply_unified_patch(repo_path: str, patch_text: str) -> tuple[bool, str]:
    """Apply a unified diff in the repo work tree."""
    if not repo_path or not (patch_text or "").strip():
        return False, "empty patch"
    result = subprocess.run(
        ["git", "-C", repo_path, "apply", "--whitespace=fix"],
        input=patch_text,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return True, ""
    return False, (result.stderr or result.stdout or "git apply failed").strip()


def changed_files_since(repo_path: str, baseline_sha: str) -> list[str]:
    if not repo_path:
        return []
    args = ["git", "-C", repo_path, "diff", "--name-only"]
    if baseline_sha:
        args.append(baseline_sha)
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def change_summary(repo_path: str, baseline_sha: str) -> dict[str, str | int]:
    """Diagnostic summary for logging and skip reasons."""
    uncommitted = has_uncommitted_changes(repo_path)
    commits = commit_count_since(repo_path, baseline_sha) if baseline_sha else 0
    stat = diff_stat_since(repo_path, baseline_sha)
    return {
        "baseline_sha": baseline_sha[:8] if baseline_sha else "(none)",
        "uncommitted": uncommitted,
        "commits_since_baseline": commits,
        "diff_stat": stat or "(no diff)",
    }
