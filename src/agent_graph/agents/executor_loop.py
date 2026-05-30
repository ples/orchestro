"""Per-repo executor loop: runs OpenHands for each target repository."""

import os
import re

from agent_graph.constrained_executor import run_constrained
from agent_graph.exceptions import ExecutorError
from agent_graph.execution_router import choose_execution_mode
from agent_graph.logging_config import step, verbose_print
from agent_graph.pr_skip import NO_CHANGES_SKIP, REQUIRED_CHANGES_MISSING
from agent_graph.repo_discovery import discovery_strict, has_resolvable_targets, run_discovery
from agent_graph.repo_execution_policy import (
    build_repo_execution_plan,
    format_execution_diagnostics,
    max_iterations_for_profile,
)
from agent_graph.state import RepoRecord, TaskState, is_follow_up_mode

from .base import BaseAgent


class ExecutorLoopAgent(BaseAgent):
    """Executes the implementation plan across multiple repositories."""

    name = "executor_loop"

    def _execute(self, state: TaskState) -> dict:
        target_repos = state.get("target_repos", [])
        plan = state.get("plan", "")
        issue = state.get("issue", "")
        input_prompt = state.get("input_prompt", "")
        follow_up = is_follow_up_mode(state)
        follow_up_prompt = (state.get("follow_up_prompt") or "").strip()

        if not target_repos:
            step("\n[Executor Loop] no repos — running without target repo")
            return _fallback_single(state)

        label = "follow-up" if follow_up else "initial"
        required_count = sum(1 for r in target_repos if r.get("requires_changes"))
        step(f"\n[Executor Loop] {len(target_repos)} repository(ies) ({label})")

        repos_dot = _extract_repos_dot(plan, target_repos)

        updated_repos: list[RepoRecord] = []
        all_errors: list[str] = []

        from agent_graph.openhands_client import (
            AgentServerSession,
            OpenHandsClient,
            reuse_agent_server,
        )

        client = OpenHandsClient()
        runtime_err = client.check_runtime_ready()
        skip_health_checks = runtime_err is None

        executable_repos = [
            r
            for r in target_repos
            if r.get("target_repo_path")
            and (follow_up or r.get("requires_changes", True))
        ]

        session: AgentServerSession | None = None
        if reuse_agent_server() and not runtime_err and len(executable_repos) > 1:
            mounts = []
            for repo_record in executable_repos:
                path = _repo_work_path(repo_record, follow_up=follow_up)
                if path and os.path.isdir(path):
                    mounts.append((path, os.path.basename(path)))
            if mounts:
                session = AgentServerSession(
                    client, mounts, skip_health_checks=skip_health_checks
                )
                try:
                    session.start()
                    verbose_print("  [Executor Loop] reusing shared agent-server")
                except Exception as exc:
                    verbose_print(
                        f"  [Executor Loop] shared agent server unavailable: {exc}"
                    )
                    session = None

        try:
            for i, repo_record in enumerate(target_repos):
                target = repo_record.get("target_repo_path", "")
                if not target:
                    verbose_print(f"  [Repo {i+1}/{len(target_repos)}] skipped (no target)")
                    updated_repos.append(repo_record)
                    continue

                step(f"  [Repo {i+1}/{len(target_repos)}] {target}")

                if not follow_up and not repo_record.get("requires_changes", False):
                    skipped = _skip_no_changes_required(repo_record)
                    step("    skipped (no changes required)")
                    updated_repos.append(skipped)
                    continue

                if runtime_err:
                    err_record = dict(repo_record)
                    err_record["pr_error"] = f"Execution skipped: {runtime_err}"
                    err_record["pr_skip_reason"] = runtime_err
                    updated_repos.append(err_record)
                    all_errors.append(f"{target}: {runtime_err}")
                    step(f"    skipped: {runtime_err}")
                    continue

                work_path = _repo_work_path(repo_record, follow_up=follow_up)
                baseline = repo_record.get("repo_baseline_sha", "")
                if work_path:
                    verbose_print(f"    using work tree: {work_path}")

                scoped_plan = build_repo_execution_plan(
                    plan, repo_record, input_prompt=input_prompt
                )
                record = dict(repo_record)
                resolvable = bool(
                    work_path
                    and has_resolvable_targets(
                        work_path, list(record.get("expected_targets") or [])
                    )
                )
                mode = choose_execution_mode(
                    record,
                    required_repo_count=required_count or 1,
                    has_resolvable_targets=resolvable,
                )
                record["execution_mode"] = mode
                verbose_print(f"    execution_mode={mode}")

                if work_path and mode != "skip":
                    discovery = run_discovery(
                        work_path,
                        list(record.get("expected_targets") or []),
                        issue_keywords=_issue_keywords(issue),
                    )
                    record["discovery_context"] = discovery.format_context()
                    if (
                        discovery_strict()
                        and record.get("requires_changes")
                        and not discovery.matched_files
                    ):
                        record["pr_error"] = (
                            f"Discovery found no files in {target} for expected targets"
                        )
                        record["execution_diagnostics"] = format_execution_diagnostics(
                            target,
                            expected_targets=list(record.get("expected_targets") or []),
                        )
                        updated_repos.append(record)
                        all_errors.append(f"{target}: {record['pr_error']}")
                        step(f"    result: {record['pr_error']}")
                        continue

                try:
                    result = _execute_repo_with_retry(
                        target_repo=target,
                        issue=issue,
                        input_prompt=input_prompt,
                        scoped_plan=scoped_plan,
                        repo_record=record,
                        repos_dot=repos_dot,
                        existing_repo_path=work_path or None,
                        baseline_sha=baseline or None,
                        skip_health_checks=skip_health_checks,
                        session=session,
                        client=client,
                        follow_up=follow_up,
                        follow_up_prompt=follow_up_prompt,
                    )
                    updated_repos.append(result)
                    err = result.get("pr_error", "")
                    step(f"    result: {err if err else 'ok'}")
                except ExecutorError as e:
                    err_record = dict(record)
                    err_record["pr_error"] = f"ExecutorError: {e}"
                    err_record["pr_skip_reason"] = f"Execution failed: {e}"
                    updated_repos.append(err_record)
                    all_errors.append(f"{target}: {e}")
                    step(f"    error: {e}")
                except RuntimeError as e:
                    err_record = dict(record)
                    err_record["pr_error"] = str(e)
                    updated_repos.append(err_record)
                    all_errors.append(f"{target}: {e}")
                    step(f"    error: {e}")
        finally:
            if session is not None:
                session.stop()

        if all_errors:
            step(f"  [Executor Loop] {len(all_errors)} repo(s) had errors")

        out: dict = {"target_repos": updated_repos}
        if follow_up:
            out["iteration"] = 1
            out["follow_up_prompt"] = ""
            out["workflow_mode"] = "initial"
        return out


def _skip_no_changes_required(repo_record: RepoRecord) -> RepoRecord:
    updated = dict(repo_record)
    work = (
        repo_record.get("planner_clone_path", "")
        or repo_record.get("work_repo_path", "")
    )
    updated["work_repo_path"] = work
    updated["pr_skip_reason"] = NO_CHANGES_SKIP
    updated["pr_error"] = ""
    updated["execution_mode"] = "skip"
    return updated


def _issue_keywords(issue: str) -> list[str]:
    words = re.findall(r"[A-Za-z]{5,}", issue or "")
    return list(dict.fromkeys(words))[:8]


def _repo_work_path(repo_record: RepoRecord, *, follow_up: bool) -> str:
    if follow_up:
        return (
            repo_record.get("work_repo_path", "")
            or repo_record.get("planner_clone_path", "")
        )
    return repo_record.get("planner_clone_path", "")


def _execute_repo_with_retry(
    target_repo: str,
    issue: str,
    scoped_plan: str,
    repo_record: RepoRecord,
    repos_dot: str = "",
    input_prompt: str = "",
    existing_repo_path: str | None = None,
    baseline_sha: str | None = None,
    skip_health_checks: bool = False,
    session=None,
    client=None,
    *,
    follow_up: bool = False,
    follow_up_prompt: str = "",
) -> RepoRecord:
    requires_changes = repo_record.get("requires_changes", False)
    expected_targets = list(repo_record.get("expected_targets") or [])
    mode = repo_record.get("execution_mode", "openhands")

    result = _run_single(
        target_repo=target_repo,
        issue=issue,
        scoped_plan=scoped_plan,
        repo_record=repo_record,
        repos_dot=repos_dot,
        input_prompt=input_prompt,
        existing_repo_path=existing_repo_path,
        baseline_sha=baseline_sha,
        skip_health_checks=skip_health_checks,
        session=session,
        client=client,
        follow_up=follow_up,
        follow_up_prompt=follow_up_prompt,
        force_openhands=mode == "openhands" and follow_up,
    )

    if not _is_required_no_op(result, requires_changes):
        return result

    verbose_print(
        f"    required repo no-op (requires_changes={requires_changes}), retrying once"
    )
    retry_plan = _build_retry_plan(scoped_plan, expected_targets)
    retry_result = _run_single(
        target_repo=target_repo,
        issue=issue,
        scoped_plan=retry_plan,
        repo_record=repo_record,
        repos_dot=repos_dot,
        input_prompt=input_prompt,
        existing_repo_path=existing_repo_path,
        baseline_sha=baseline_sha,
        skip_health_checks=skip_health_checks,
        session=session,
        client=client,
        follow_up=follow_up,
        follow_up_prompt=follow_up_prompt,
        retry_attempt=1,
        force_openhands=True,
    )

    if not _is_required_no_op(retry_result, requires_changes):
        return retry_result

    return _mark_required_no_op_failure(
        retry_result,
        target_repo=target_repo,
        expected_targets=expected_targets,
        retry_attempt=1,
    )


def _is_required_no_op(repo_record: RepoRecord, requires_changes: bool) -> bool:
    if not requires_changes:
        return False
    skip = repo_record.get("pr_skip_reason", "")
    if skip in (NO_CHANGES_SKIP, REQUIRED_CHANGES_MISSING):
        return True
    return not repo_record.get("pr_error") and not repo_record.get("change_stat")


def _build_retry_plan(plan: str, expected_targets: list[str]) -> str:
    targets_block = (
        "\n".join(f"- {t}" for t in expected_targets)
        if expected_targets
        else "- (search repo for components/files from the plan)"
    )
    return (
        f"{plan}\n\n"
        "## RETRY — required changes missing\n\n"
        "The previous run produced **no file changes**. You must either:\n"
        "1. Apply the planned code edits and leave a non-empty git diff, or\n"
        "2. Report a concrete blocker (exact file searched, what was missing).\n\n"
        "### Expected targets\n"
        f"{targets_block}\n"
    )


def _mark_required_no_op_failure(
    repo_record: RepoRecord,
    *,
    target_repo: str,
    expected_targets: list[str],
    retry_attempt: int,
) -> RepoRecord:
    updated = dict(repo_record)
    summary = updated.get("repo_summary", "")
    updated["pr_skip_reason"] = ""
    updated["pr_error"] = (
        f"Required changes missing in {target_repo} "
        f"(no file changes after {retry_attempt + 1} attempt(s))"
    )
    updated["execution_diagnostics"] = format_execution_diagnostics(
        target_repo,
        expected_targets=expected_targets,
        openhands_summary=summary,
        retry_attempt=retry_attempt,
    )
    verbose_print(f"    execution_diagnostics:\n{updated['execution_diagnostics']}")
    return updated


def _run_single(
    target_repo: str,
    issue: str,
    scoped_plan: str,
    repo_record: RepoRecord,
    repos_dot: str = "",
    input_prompt: str = "",
    existing_repo_path: str | None = None,
    baseline_sha: str | None = None,
    skip_health_checks: bool = False,
    session=None,
    client=None,
    *,
    follow_up: bool = False,
    follow_up_prompt: str = "",
    retry_attempt: int = 0,
    force_openhands: bool = False,
) -> RepoRecord:
    from agent_graph.openhands_client import OpenHandsClient
    from agent_graph.repo_discovery import DiscoveryResult

    if client is None:
        client = OpenHandsClient()

    profile = repo_record.get("execution_profile", "standard")
    max_iters = max_iterations_for_profile(profile)  # type: ignore[arg-type]
    expected_targets = list(repo_record.get("expected_targets") or [])
    mode = repo_record.get("execution_mode", "openhands")
    discovery_ctx = repo_record.get("discovery_context", "")
    full_plan = scoped_plan
    if discovery_ctx:
        full_plan = f"{discovery_ctx}\n\n{scoped_plan}"

    execution_result = None
    if (
        not force_openhands
        and not follow_up
        and mode == "constrained"
        and existing_repo_path
    ):
        discovery = DiscoveryResult()
        if discovery_ctx:
            discovery = _discovery_from_context(discovery_ctx, existing_repo_path)
        execution_result = run_constrained(
            issue=issue,
            scoped_plan=scoped_plan,
            repo_path=existing_repo_path,
            baseline_sha=baseline_sha or "",
            discovery=discovery,
            input_prompt=input_prompt,
        )
        if execution_result.success and not execution_result.no_changes:
            repo_record = dict(repo_record)
            repo_record["execution_mode"] = "constrained"
            return _apply_execution_result(
                repo_record,
                execution_result,
                target_repo=target_repo,
                baseline_sha=baseline_sha,
                requires_changes=repo_record.get("requires_changes", False),
                retry_attempt=retry_attempt,
            )
        verbose_print(
            f"    constrained path failed: {execution_result.summary[:120]}, "
            "falling back to OpenHands"
        )
        repo_record = dict(repo_record)
        repo_record["execution_mode"] = "constrained_fallback"

    repos_note = repos_dot.strip() if repos_dot else ""
    if repos_note:
        repos_note = (
            f"\n\nAffected repositories:\n{repos_note}"
            f"\n\nApply **all changes to this repository: {target_repo}**"
        )

    diff_stat = repo_record.get("change_stat", "") if follow_up else ""
    result = client.run_task(
        target_repo=target_repo,
        issue=issue,
        plan=full_plan + repos_note,
        existing_repo_path=existing_repo_path,
        baseline_sha=baseline_sha,
        skip_health_checks=skip_health_checks,
        input_prompt=input_prompt,
        session=session,
        follow_up=follow_up,
        follow_up_prompt=follow_up_prompt,
        diff_stat=diff_stat,
        max_iterations=max_iters,
        expected_targets=expected_targets,
        scoped_plan=full_plan,
    )

    return _apply_execution_result(
        repo_record,
        result,
        target_repo=target_repo,
        baseline_sha=baseline_sha,
        requires_changes=repo_record.get("requires_changes", False),
        retry_attempt=retry_attempt,
    )


def _discovery_from_context(discovery_ctx: str, repo_path: str) -> "DiscoveryResult":
    from agent_graph.repo_discovery import DiscoveryResult

    files: list[str] = []
    for line in discovery_ctx.splitlines():
        line = line.strip()
        if line.startswith("- ") and not line.startswith("- ("):
            rel = line[2:].strip()
            if rel and not rel.startswith("#"):
                files.append(rel)
    if not files:
        return run_discovery(repo_path, [])
    return DiscoveryResult(matched_files=files, grep_snippets=discovery_ctx)


def _apply_execution_result(
    repo_record: RepoRecord,
    result,
    *,
    target_repo: str,
    baseline_sha: str | None,
    requires_changes: bool,
    retry_attempt: int,
) -> RepoRecord:
    updated = dict(repo_record)
    updated["work_repo_path"] = result.work_repo_path or updated.get("work_repo_path", "")
    updated["repo_baseline_sha"] = result.repo_baseline_sha or baseline_sha or ""
    updated["diff_patch"] = result.diff_patch or ""
    updated["change_stat"] = result.change_stat or ""

    if result.no_changes:
        if requires_changes:
            updated["pr_skip_reason"] = REQUIRED_CHANGES_MISSING
            updated["pr_error"] = ""
        else:
            updated["pr_skip_reason"] = NO_CHANGES_SKIP
            updated["pr_error"] = ""
        if result.summary:
            updated["repo_summary"] = result.summary
        if requires_changes:
            updated["execution_diagnostics"] = format_execution_diagnostics(
                target_repo,
                expected_targets=list(repo_record.get("expected_targets") or []),
                openhands_summary=result.summary or "",
                retry_attempt=retry_attempt,
            )
            verbose_print(
                f"    no_changes requires_changes={requires_changes} "
                f"retry_attempt={retry_attempt}"
            )
    elif not result.success:
        updated["pr_error"] = result.summary or "OpenHands execution failed"
        updated["pr_skip_reason"] = result.summary or "Executor returned failure"
    else:
        updated["repo_summary"] = result.summary or updated.get("repo_summary", "")

    return updated


def _fallback_single(state: TaskState) -> dict:
    follow_up = is_follow_up_mode(state)
    plan = state.get("plan", "")
    return {
        "target_repos": [
            _run_single(
                target_repo=state.get("target_repo_path", "")
                or state.get("github_issue_url", "")
                or "",
                issue=state.get("issue", ""),
                input_prompt=state.get("input_prompt", ""),
                scoped_plan=plan,
                repo_record={},
                follow_up=follow_up,
                follow_up_prompt=(state.get("follow_up_prompt") or "").strip(),
                force_openhands=True,
            )
        ]
    }


def _extract_repos_dot(plan: str, target_repos: list[RepoRecord]) -> str:
    lines = []
    for r in target_repos:
        url = r.get("target_repo_path", "")
        summary = r.get("repo_summary", "")
        if url and summary:
            lines.append(f"- **{url}**: {summary}")
    if lines:
        return (
            "**Multiple repositories to update.\n"
            "For each of the following repos, scan the code, create a sub-plan, "
            "and implement all changes.**\n"
        ) + "\n".join(lines)
    return ""
