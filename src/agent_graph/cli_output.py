"""Colored terminal output for workflow results."""

import os
import sys
from typing import Any

from agent_graph.pr_skip import NO_CHANGES_SKIP, is_no_changes_summary
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

    target_repos = state.get("target_repos", [])
    if target_repos:
        lines.append(_paint("Repos", _C.BOLD))
        for r in target_repos:
            url = r.get("target_repo_path", "")
            pr = r.get("pr_url", "")
            err = r.get("pr_error", "")
            skip = r.get("pr_skip_reason", "")
            deploy_tag = r.get("deploy_tag_name", "")
            deploy_tag_err = r.get("deploy_tag_error", "")
            push_mode = r.get("pr_push_mode", "")
            if pr:
                status = f"  ✓ {pr}"
            elif err and not is_no_changes_summary(err):
                status = f"  ✗ {err}"
            elif skip == NO_CHANGES_SKIP or is_no_changes_summary(skip) or is_no_changes_summary(err):
                status = "  ○ skipped (no changes)"
            elif skip:
                status = f"  ○ skipped: {skip}"
            else:
                status = ""
            parts = [url]
            if status:
                parts.append(status.strip())
            if deploy_tag:
                parts.append(f"tag: {deploy_tag}")
            if deploy_tag_err:
                parts.append(f"tag error: {deploy_tag_err}")
            if push_mode:
                parts.append(f"push: {push_mode}")
            lines.append("  " + " | ".join(parts))

    impl = state.get("implementation_result", "")
    if impl:
        if "no file changes" in impl.lower() or "without editing" in impl.lower():
            lines.append(_paint("Executor", _C.BOLD, _C.RED) + f"  {impl}")
        else:
            lines.append(_paint("Executor", _C.BOLD) + f"  {impl}")

    verify = state.get("verification_result", "")
    if verify:
        lines.append(_paint("Verifier", _C.BOLD) + f"  {verify}")

    for r in target_repos:
        work_path = r.get("work_repo_path", "")
        if work_path:
            lines.append(_paint("Worktree", _C.BOLD) + f"  {_paint(work_path, _C.DIM)}")
            baseline = r.get("repo_baseline_sha", "")
            if baseline:
                lines.append(
                    _paint("Baseline", _C.BOLD) + f"  {_paint(baseline[:12], _C.DIM)}"
                )
            diff_stat = r.get("change_stat", "")
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
        for url in pr_url.split("\n"):
            lines.append(f"  {url}")
    if pr_error:
        lines.append(_paint("✗ PR creation failed", _C.BOLD, _C.RED))
        for err in pr_error.split("\n"):
            lines.append(f"  {err}")
    if skip:
        lines.append(_paint("○ PR creation skipped", _C.BOLD, _C.YELLOW))
        for skip_line in skip.split("\n"):
            lines.append(f"  {skip_line}")
    if not pr_url and not pr_error and not skip:
        lines.append(_paint("○ No pull request", _C.BOLD, _C.YELLOW))
        lines.append("  Workflow finished without opening a PR.")

    deploy_tag_name = state.get("deploy_tag_name", "")
    deploy_tag_error = state.get("deploy_tag_error", "")
    deploy_env = state.get("deploy_env", "")
    deploy_env_source = state.get("deploy_env_source", "")
    pr_push_mode = state.get("pr_push_mode", "")
    if deploy_env:
        if deploy_env_source:
            lines.append(
                _paint("Deploy env", _C.BOLD) + f"  {deploy_env} ({deploy_env_source})"
            )
        else:
            lines.append(_paint("Deploy env", _C.BOLD) + f"  {deploy_env}")
    if deploy_tag_name:
        lines.append(_paint("✓ Deploy tag pushed", _C.BOLD, _C.GREEN))
        for tag_line in deploy_tag_name.split("\n"):
            lines.append(f"  {tag_line}")
    if deploy_tag_error:
        lines.append(_paint("✗ Deploy tag failed", _C.BOLD, _C.RED))
        for tag_err_line in deploy_tag_error.split("\n"):
            lines.append(f"  {tag_err_line}")
    if pr_push_mode:
        lines.append(_paint("Push mode", _C.BOLD) + f"  {pr_push_mode}")

    lines.append(sep)
    lines.append("")
    return "\n".join(lines)


def print_final_result(state: dict[str, Any]) -> None:
    print(format_final_result(state))
