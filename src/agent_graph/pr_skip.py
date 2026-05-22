"""Shared helpers for PR skip vs failure."""

NO_CHANGES_SKIP = "no_changes"


def is_no_changes_summary(text: str) -> bool:
    lower = (text or "").lower()
    return "no file changes" in lower or "without editing" in lower


def format_no_changes_skip(target_repo_path: str) -> str:
    return f"{target_repo_path}: No changes required"
