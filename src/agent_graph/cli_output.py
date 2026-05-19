"""Colored terminal output for workflow results."""

import os
import sys
from typing import Any

from agent_graph.state import TaskState


class _C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"


def _supports_color() -> bool:
    return sys.stdout.isatty() and os.getenv("NO_COLOR") is None


def _paint(text: str, *codes: str) -> str:
    if not _supports_color():
        return text
    prefix = "".join(codes)
    return f"{prefix}{text}{_C.RESET}"


def _first_line(text: str, max_len: int = 80) -> str:
    line = (text or "").strip().split("\n")[0]
    if len(line) > max_len:
        return line[: max_len - 3] + "..."
    return line


def format_final_result(state: TaskState) -> str:
    """Render a human-readable summary of the workflow result."""
    lines: list[str] = []
    sep = _paint("═" * 56, _C.DIM)

    lines.append("")
    lines.append(sep)
    lines.append(_paint("  WORKFLOW RESULT", _C.BOLD, _C.CYAN))
    lines.append(sep)

    issue = _first_line(state.get("issue", ""))
    if issue:
        lines.append(_paint("Issue", _C.BOLD) + f"     {issue}")

    github = state.get("github_issue_url", "")
    if github:
        lines.append(_paint("GitHub", _C.BOLD) + f"    {github}")

    impl = state.get("implementation_result", "")
    if impl:
        if "no file changes" in impl.lower() or "without editing" in impl.lower():
            lines.append(_paint("Executor", _C.BOLD, _C.RED) + f"  {impl}")
        else:
            lines.append(_paint("Executor", _C.BOLD) + f"  {impl}")

    verify = state.get("verification_result", "")
    if verify:
        lines.append(_paint("Verifier", _C.BOLD) + f"  {verify}")

    work_path = state.get("work_repo_path", "")
    if work_path:
        lines.append(_paint("Worktree", _C.BOLD) + f"  {_paint(work_path, _C.DIM)}")

    baseline = state.get("repo_baseline_sha", "")
    if baseline:
        lines.append(
            _paint("Baseline", _C.BOLD) + f"  {_paint(baseline[:12], _C.DIM)}"
        )

    diff_stat = state.get("change_stat", "")
    if diff_stat:
        lines.append("")
        lines.append(_paint("Changes", _C.BOLD, _C.YELLOW))
        for stat_line in diff_stat.splitlines():
            lines.append(f"  {stat_line}")

    lines.append("")
    pr_url = state.get("pr_url", "")
    pr_error = state.get("pr_error", "")
    skip = state.get("pr_skip_reason", "")

    if pr_url:
        lines.append(_paint("✓ Pull request created", _C.BOLD, _C.GREEN))
        lines.append(f"  {pr_url}")
    elif pr_error:
        lines.append(_paint("✗ PR creation failed", _C.BOLD, _C.RED))
        lines.append(f"  {pr_error}")
    elif skip:
        lines.append(_paint("○ PR skipped", _C.BOLD, _C.YELLOW))
        for skip_line in skip.split("\n"):
            lines.append(f"  {skip_line}")
        if "no changes" in skip.lower() and "commits_since_baseline=0" not in skip:
            lines.append(
                _paint(
                    "  Tip: OpenHands may have stopped without editing files "
                    "(check executor logs for tool availability).",
                    _C.DIM,
                )
            )
    else:
        lines.append(_paint("○ No pull request", _C.BOLD, _C.YELLOW))
        lines.append("  Workflow finished without opening a PR.")

    lines.append(sep)
    lines.append("")
    return "\n".join(lines)


def print_final_result(state: dict[str, Any]) -> None:
    print(format_final_result(state))
