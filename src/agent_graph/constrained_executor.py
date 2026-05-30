"""Fast path: local LLM patch for simple localized changes."""

from __future__ import annotations

import os
import re

from agent_graph.git_utils import (
    apply_unified_patch,
    capture_diff_since,
    change_summary,
    changed_files_since,
    diff_stat_since,
    has_changes_since,
)
from agent_graph.models import ExecutionResult
from agent_graph.openhands_client import OpenHandsClient
from agent_graph.repo_discovery import DiscoveryResult


def constrained_max_files() -> int:
    raw = os.getenv("EXECUTOR_CONSTRAINED_MAX_FILES", "3").strip()
    if raw.isdigit():
        return int(raw)
    return 3


def run_constrained(
    *,
    issue: str,
    scoped_plan: str,
    repo_path: str,
    baseline_sha: str,
    discovery: DiscoveryResult,
    input_prompt: str = "",
) -> ExecutionResult:
    if not repo_path or not os.path.isdir(repo_path):
        return ExecutionResult(success=False, summary="constrained: no repo path")

    files = discovery.matched_files[: constrained_max_files()]
    if not files:
        return ExecutionResult(
            success=False,
            summary="constrained: no matched files for patch",
        )

    file_blocks: list[str] = []
    for rel in files:
        abs_path = os.path.join(repo_path, rel)
        if not os.path.isfile(abs_path):
            continue
        try:
            with open(abs_path, errors="ignore") as f:
                content = f.read(120_000)
        except OSError:
            continue
        file_blocks.append(f"### File: {rel}\n```\n{content}\n```")

    if not file_blocks:
        return ExecutionResult(
            success=False,
            summary="constrained: could not read matched files",
        )

    discovery_block = discovery.format_context()
    prompt = (
        f"# Task\n\n{issue}\n\n"
        f"# Scoped plan\n\n{scoped_plan}\n\n"
        f"{discovery_block}\n\n"
        f"# Files to edit\n\n"
        + "\n\n".join(file_blocks)
        + "\n\n"
        "Output a single unified diff (`git diff` format) that implements the scoped plan. "
        "Only modify the listed files. "
        "If impossible, reply with `BLOCKER:` and a one-line reason."
    )

    client = OpenHandsClient()
    response = client._plan_with_local_llm(prompt)
    if not response.strip():
        return ExecutionResult(success=False, summary="constrained: empty LLM response")

    if response.strip().upper().startswith("BLOCKER:"):
        return ExecutionResult(success=False, summary=response.strip()[:500])

    patch = _extract_patch(response)
    if not patch:
        return ExecutionResult(success=False, summary="constrained: no patch in LLM response")

    ok, err = apply_unified_patch(repo_path, patch)
    if not ok:
        return ExecutionResult(
            success=False,
            summary=f"constrained: patch apply failed: {err}",
        )

    if baseline_sha and not has_changes_since(repo_path, baseline_sha):
        return ExecutionResult(
            success=False,
            summary="constrained: patch applied but no diff vs baseline",
            no_changes=True,
            work_repo_path=repo_path,
            repo_baseline_sha=baseline_sha,
        )

    touched = changed_files_since(repo_path, baseline_sha)
    if files and not _touches_expected(touched, files):
        return ExecutionResult(
            success=False,
            summary=(
                f"constrained: diff does not touch expected files "
                f"(touched={touched}, expected={files})"
            ),
            work_repo_path=repo_path,
            repo_baseline_sha=baseline_sha,
        )

    stat = change_summary(repo_path, baseline_sha)
    return ExecutionResult(
        success=True,
        summary=(
            f"constrained execution complete "
            f"(uncommitted={stat['uncommitted']}, commits={stat['commits_since_baseline']})"
        ),
        work_repo_path=repo_path,
        repo_baseline_sha=baseline_sha,
        diff_patch=capture_diff_since(repo_path, baseline_sha),
        change_stat=diff_stat_since(repo_path, baseline_sha),
    )


def _extract_patch(text: str) -> str:
    fence = re.search(r"```(?:diff)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fence:
        body = fence.group(1).strip()
        if body.startswith("diff ") or body.startswith("--- "):
            return body
    if text.strip().startswith("diff ") or text.strip().startswith("--- "):
        return text.strip()
    return ""


def _touches_expected(touched: list[str], expected: list[str]) -> bool:
    if not touched:
        return False
    expected_names = {os.path.basename(e) for e in expected}
    for path in touched:
        if path in expected or os.path.basename(path) in expected_names:
            return True
    return len(touched) > 0 and not expected
