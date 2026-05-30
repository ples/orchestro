"""Infer per-repo execution requirements from planner output."""

from __future__ import annotations

import os
import re
from typing import Literal

from agent_graph.state import RepoRecord

ExecutionProfile = Literal["simple", "standard", "complex"]

_NO_CHANGE_PATTERNS = (
    r"no\s+action\s+required",
    r"no\s+changes?\s+(are\s+)?required",
    r"no\s+changes?\s+needed",
    r"no\s+code\s+changes?\s+needed",
    r"no\s+updates?\s+required",
    r"no\s+backend\s+modifications?",
    r"no\s+frontend\s+modifications?",
    r"no\s+changes?\s+required",
    r"not\s+requiring\s+code\s+changes",
    r"no\s+changes?\s+in\s+this\s+repo",
    r"requires?\s+no\s+changes?",
    r"context[- ]only",
)

_CHANGE_REQUIRED_PATTERNS = (
    r"requiring\s+code\s+changes",
    r"only\s+repository\s+requiring",
    r"requires?\s+code\s+changes",
    r"changes?\s+are\s+required",
    r"implementation\s+required",
)

_FILE_HINT_RE = re.compile(
    r"`([^`]+\.(?:tsx?|jsx?|py|java|go|rs|rb|php|vue|svelte))`"
    r"|(?:^|\s)((?:src|app|lib|components?)/[\w./-]+\.(?:tsx?|jsx?|py))"
    r"|([A-Z][A-Za-z0-9]+(?:Details|Overview|Stats|Management|Component)\.tsx?)",
    re.MULTILINE,
)


def extract_expected_targets(repo_summary: str, *, limit: int = 8) -> list[str]:
    """Pull file/component hints from per-repo planner summary."""
    if not (repo_summary or "").strip():
        return []
    targets: list[str] = []
    seen: set[str] = set()
    for section in ("Implementation steps", "Proposed changes", "Findings"):
        body = _extract_section(repo_summary, section)
        if not body:
            continue
        for match in _FILE_HINT_RE.finditer(body):
            hint = next(g for g in match.groups() if g)
            hint = hint.strip().strip("`")
            if hint and hint not in seen:
                seen.add(hint)
                targets.append(hint)
            if len(targets) >= limit:
                return targets
    return targets


def infer_requires_changes(
    repo_url: str,
    repo_summary: str,
    *,
    cross_repo_context: str = "",
    all_repo_urls: list[str] | None = None,
) -> bool:
    """True when planner output indicates this repo should produce a diff."""
    repo_name = _repo_basename(repo_url)
    summary = (repo_summary or "").strip()
    overview = (cross_repo_context or "").strip()

    if overview and repo_name:
        role_body = _extract_section(overview, "Repo roles") or overview
        for line in role_body.splitlines():
            if repo_name not in line.lower() and repo_url not in line:
                continue
            lower = line.lower()
            if any(re.search(p, lower) for p in _NO_CHANGE_PATTERNS):
                return False
            if any(re.search(p, lower) for p in _CHANGE_REQUIRED_PATTERNS):
                return True

    if summary:
        lower = summary.lower()
        if any(re.search(p, lower) for p in _NO_CHANGE_PATTERNS):
            return False
        for heading in ("Implementation steps", "Proposed changes"):
            body = _extract_section(summary, heading)
            if body and not _section_is_no_op(body):
                return True
        if any(re.search(p, lower) for p in _CHANGE_REQUIRED_PATTERNS):
            return True

    if all_repo_urls and len(all_repo_urls) == 1:
        return True

    return False


def apply_execution_contract(
    target_repos: list[RepoRecord],
    *,
    cross_repo_context: str = "",
    input_prompt: str = "",
) -> list[RepoRecord]:
    """Set requires_changes and expected_targets on each repo record."""
    urls = [
        r.get("target_repo_path", "")
        for r in target_repos
        if r.get("target_repo_path")
    ]
    prompt_focus = _prompt_focused_repos(urls, input_prompt)
    strict_prompt_focus = len(prompt_focus) == 1
    required_count = 0
    updated: list[RepoRecord] = []
    for repo in target_repos:
        record: RepoRecord = dict(repo)
        url = record.get("target_repo_path", "")
        summary = record.get("repo_summary", "")
        record["expected_targets"] = extract_expected_targets(summary)
        record["requires_changes"] = infer_requires_changes(
            url,
            summary,
            cross_repo_context=cross_repo_context,
            all_repo_urls=urls,
        )
        if strict_prompt_focus:
            repo_name = _repo_basename(url)
            record["requires_changes"] = repo_name in prompt_focus
        elif prompt_focus:
            repo_name = _repo_basename(url)
            if repo_name not in prompt_focus and not _explicit_change_required(
                summary, cross_repo_context
            ):
                record["requires_changes"] = False
        if record["requires_changes"]:
            required_count += 1
        updated.append(record)
    for record in updated:
        record["execution_profile"] = infer_execution_profile(
            record, required_repo_count=required_count
        )
    return updated


def build_repo_execution_plan(
    plan: str,
    repo_record: RepoRecord,
    *,
    input_prompt: str = "",
) -> str:
    """Repo-scoped plan: per-repo steps and targets, not the full cross-repo dump."""
    url = repo_record.get("target_repo_path", "")
    summary = (repo_record.get("repo_summary") or "").strip()
    targets = list(repo_record.get("expected_targets") or [])
    parts: list[str] = ["## Repository scope", "", f"Apply changes **only** in: {url}"]
    parts.append("Do not modify other repositories.")
    if (input_prompt or "").strip():
        parts.extend(
            [
                "",
                "## Developer instructions",
                "",
                input_prompt.strip(),
            ]
        )
    repo_sections: list[str] = []
    for heading in ("Implementation steps", "Proposed changes", "Findings"):
        body = _extract_section(summary, heading)
        if body:
            repo_sections.append(f"### {heading}\n{body}")
    if repo_sections:
        parts.extend(["", "## Repo-specific plan", ""] + repo_sections)
    elif summary:
        parts.extend(["", "## Repo analysis", "", summary[:8000]])
    if targets:
        parts.extend(
            [
                "",
                "## Expected targets",
                "",
                "\n".join(f"- `{t}`" for t in targets),
            ]
        )
    global_steps = _extract_section(plan, "Implementation plan")
    if global_steps and url and url in global_steps:
        parts.extend(["", "## Cross-repo steps (this repo only)", "", global_steps[:4000]])
    return "\n".join(parts).strip()


def build_discovery_instructions(expected_targets: list[str]) -> str:
    """Discovery steps prepended to OpenHands execution prompts."""
    if expected_targets:
        targets = "\n".join(f"  - `{t}`" for t in expected_targets)
        search_hint = "Search for each expected target (file path or symbol)."
    else:
        targets = "  - (use keywords from the issue and plan)"
        search_hint = "Search using issue/plan keywords before editing."
    return (
        "## Discovery (required before editing)\n\n"
        f"1. {search_hint}\n"
        f"2. Run `grep -rni` in the repo for:\n{targets}\n"
        "3. List matched file paths in your response before making edits.\n"
        "4. Edit only matched files unless you document a concrete blocker.\n"
    )


def infer_execution_profile(
    repo_record: RepoRecord,
    *,
    required_repo_count: int = 1,
) -> ExecutionProfile:
    if not repo_record.get("requires_changes", False):
        return "standard"
    summary = (repo_record.get("repo_summary") or "").lower()
    targets = repo_record.get("expected_targets") or []
    complex_markers = (
        "refactor",
        "migration",
        "api contract",
        "breaking change",
        "multiple services",
        "cross-repo",
    )
    if required_repo_count > 1 or any(m in summary for m in complex_markers):
        return "complex"
    simple_verbs = ("remove ", "hide ", "delete ", "fix ", "display ", "render ")
    if (
        len(targets) <= 3
        and required_repo_count == 1
        and any(v in summary for v in simple_verbs)
    ):
        return "simple"
    return "standard"


def max_iterations_for_profile(profile: ExecutionProfile) -> int:
    defaults = {
        "simple": 60,
        "standard": 120,
        "complex": 500,
    }
    env_keys = {
        "simple": "EXECUTOR_SIMPLE_MAX_ITERATIONS",
        "standard": "EXECUTOR_MAX_ITERATIONS",
        "complex": "EXECUTOR_COMPLEX_MAX_ITERATIONS",
    }
    raw = os.getenv(env_keys.get(profile, ""), "").strip()
    if raw.isdigit():
        return int(raw)
    return defaults[profile]


def format_execution_diagnostics(
    repo_url: str,
    *,
    expected_targets: list[str] | None = None,
    openhands_summary: str = "",
    retry_attempt: int = 0,
) -> str:
    lines = [f"repo: {repo_url}"]
    if retry_attempt:
        lines.append(f"retry_attempt: {retry_attempt}")
    if expected_targets:
        lines.append(f"expected_targets: {', '.join(expected_targets)}")
    if openhands_summary:
        lines.append(f"openhands: {openhands_summary.strip()[:500]}")
    return "\n".join(lines)


def _extract_section(text: str, heading: str) -> str:
    pattern = re.compile(
        rf"^#{{1,4}}\s+{re.escape(heading)}\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(text)
    if not match:
        return ""
    start = match.end()
    next_heading = re.search(r"^#{1,4}\s+", text[start:], re.MULTILINE)
    end = start + next_heading.start() if next_heading else len(text)
    return text[start:end].strip()


def _section_is_no_op(body: str) -> bool:
    lower = body.lower()
    return any(re.search(p, lower) for p in _NO_CHANGE_PATTERNS) and not any(
        verb in lower
        for verb in ("remove ", "modify ", "update ", "add ", "delete ", "fix ")
    )


def _repo_basename(repo_url: str) -> str:
    path = (repo_url or "").rstrip("/").split("/")[-1]
    return path.removesuffix(".git").lower()


def _prompt_focused_repos(repo_urls: list[str], input_prompt: str) -> set[str]:
    text = (input_prompt or "").strip().lower()
    if not text:
        return set()
    focused: set[str] = set()
    for url in repo_urls:
        name = _repo_basename(url)
        if not name:
            continue
        spaced = name.replace("-", " ")
        compact = name.replace("-", "")
        if name in text or spaced in text or compact in text:
            focused.add(name)
    return focused


def _explicit_change_required(repo_summary: str, cross_repo_context: str) -> bool:
    text = f"{repo_summary}\n{cross_repo_context}".lower()
    return any(re.search(p, text) for p in _CHANGE_REQUIRED_PATTERNS)
