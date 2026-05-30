"""Host-side pre-flight discovery before execution."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field

_SOURCE_GLOBS = (
    "--include=*.ts",
    "--include=*.tsx",
    "--include=*.js",
    "--include=*.jsx",
    "--include=*.py",
    "--include=*.java",
    "--include=*.vue",
)


@dataclass
class DiscoveryResult:
    matched_files: list[str] = field(default_factory=list)
    grep_snippets: str = ""

    def format_context(self) -> str:
        lines = ["## Pre-flight discovery"]
        if self.matched_files:
            lines.append("")
            lines.append("Matched files:")
            for path in self.matched_files[:20]:
                lines.append(f"- {path}")
        if self.grep_snippets.strip():
            lines.extend(["", "### grep output", "", self.grep_snippets.strip()[:4000]])
        return "\n".join(lines)


def discovery_strict() -> bool:
    raw = os.getenv("EXECUTOR_DISCOVERY_STRICT", "0").strip().lower()
    return raw in ("1", "true", "yes")


def run_discovery(
    repo_path: str,
    expected_targets: list[str],
    *,
    issue_keywords: list[str] | None = None,
) -> DiscoveryResult:
    if not repo_path or not os.path.isdir(repo_path):
        return DiscoveryResult()

    keywords: list[str] = []
    for target in expected_targets:
        keywords.extend(_keywords_from_target(target))
    for kw in issue_keywords or []:
        if kw and kw.lower() not in {k.lower() for k in keywords}:
            keywords.append(kw)
    keywords = list(dict.fromkeys(keywords))[:12]

    matched: list[str] = []
    snippets: list[str] = []
    for kw in keywords:
        files, snippet = _grep_repo(repo_path, kw)
        for fpath in files:
            if fpath not in matched:
                matched.append(fpath)
        if snippet:
            snippets.append(f"### grep '{kw}'\n```\n{snippet}\n```")

    if not matched:
        for target in expected_targets:
            resolved = _resolve_path(repo_path, target)
            if resolved and resolved not in matched:
                matched.append(resolved)

    return DiscoveryResult(
        matched_files=matched[:30],
        grep_snippets="\n\n".join(snippets[:8]),
    )


def has_resolvable_targets(repo_path: str, expected_targets: list[str]) -> bool:
    if not expected_targets:
        return False
    discovery = run_discovery(repo_path, expected_targets)
    return bool(discovery.matched_files)


def _keywords_from_target(target: str) -> list[str]:
    target = target.strip().strip("`")
    if not target:
        return []
    parts = [target]
    base = os.path.basename(target)
    if base and base != target:
        parts.append(base)
    stem = os.path.splitext(base)[0] if base else ""
    if stem and len(stem) > 3:
        parts.append(stem)
    return parts


def _resolve_path(repo_path: str, target: str) -> str:
    target = target.strip().strip("`")
    if not target:
        return ""
    candidates = [target, os.path.join(repo_path, target)]
    for candidate in candidates:
        if os.path.isfile(candidate):
            try:
                return os.path.relpath(candidate, repo_path)
            except ValueError:
                return candidate
    name = os.path.basename(target)
    if not name:
        return ""
    try:
        result = subprocess.run(
            ["find", repo_path, "-name", name, "-type", "f"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    if result.returncode != 0 or not result.stdout.strip():
        return ""
    first = result.stdout.strip().splitlines()[0]
    try:
        return os.path.relpath(first, repo_path)
    except ValueError:
        return first


def _grep_repo(repo_path: str, keyword: str) -> tuple[list[str], str]:
    if len(keyword) < 2:
        return [], ""
    try:
        result = subprocess.run(
            ["grep", "-rni", *_SOURCE_GLOBS, "-m", "3", keyword, repo_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        return [], ""
    if not result.stdout.strip():
        return [], ""
    files: list[str] = []
    for line in result.stdout.strip().splitlines():
        if ":" not in line:
            continue
        rel = line.split(":", 1)[0]
        try:
            rel_path = os.path.relpath(rel, repo_path)
        except ValueError:
            rel_path = rel
        if rel_path not in files:
            files.append(rel_path)
    return files, result.stdout.strip()[:2000]
