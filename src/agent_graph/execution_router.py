"""Choose execution mode per repository."""

from __future__ import annotations

import os
from typing import Literal

from agent_graph.repo_execution_policy import infer_execution_profile
from agent_graph.state import RepoRecord

ExecutionMode = Literal["skip", "constrained", "openhands"]


def constrained_enabled() -> bool:
    raw = os.getenv("EXECUTOR_CONSTRAINED_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no")


def choose_execution_mode(
    repo_record: RepoRecord,
    *,
    required_repo_count: int = 1,
    has_resolvable_targets: bool = False,
) -> ExecutionMode:
    if not repo_record.get("requires_changes", False):
        return "skip"
    profile = repo_record.get("execution_profile") or infer_execution_profile(
        repo_record, required_repo_count=required_repo_count
    )
    if (
        constrained_enabled()
        and profile == "simple"
        and has_resolvable_targets
    ):
        return "constrained"
    return "openhands"
