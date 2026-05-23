"""Per-repo executor loop: runs OpenHands for each target repository."""

import os

from agent_graph.exceptions import ExecutorError
from agent_graph.logging_config import step, verbose_print
from agent_graph.pr_skip import NO_CHANGES_SKIP
from agent_graph.state import RepoRecord, TaskState

from .base import BaseAgent


class ExecutorLoopAgent(BaseAgent):
    """Executes the implementation plan across multiple repositories."""

    name = "executor_loop"

    def _execute(self, state: TaskState) -> dict:
        target_repos = state.get("target_repos", [])
        plan = state.get("plan", "")
        issue = state.get("issue", "")
        input_prompt = state.get("input_prompt", "")

        if not target_repos:
            step("\n[Executor Loop] no repos — running without target repo")
            return _fallback_single(state)

        step(f"\n[Executor Loop] {len(target_repos)} repository(ies)")

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

        session: AgentServerSession | None = None
        if reuse_agent_server() and not runtime_err and len(target_repos) > 1:
            mounts = []
            for repo_record in target_repos:
                clone_path = repo_record.get("planner_clone_path", "")
                if clone_path and os.path.isdir(clone_path):
                    mounts.append((clone_path, os.path.basename(clone_path)))
            if mounts:
                session = AgentServerSession(
                    client, mounts, skip_health_checks=skip_health_checks
                )
                try:
                    session.start()
                    verbose_print("  [Executor Loop] reusing shared agent-server")
                except Exception as exc:
                    verbose_print(f"  [Executor Loop] shared agent server unavailable: {exc}")
                    session = None

        try:
            for i, repo_record in enumerate(target_repos):
                target = repo_record.get("target_repo_path", "")
                summary = repo_record.get("repo_summary", "")
                if not target:
                    verbose_print(f"  [Repo {i+1}/{len(target_repos)}] skipped (no target)")
                    updated_repos.append(repo_record)
                    continue

                step(f"  [Repo {i+1}/{len(target_repos)}] {target}")

                if runtime_err:
                    err_record = dict(repo_record)
                    err_record["pr_error"] = f"Execution skipped: {runtime_err}"
                    err_record["pr_skip_reason"] = runtime_err
                    updated_repos.append(err_record)
                    all_errors.append(f"{target}: {runtime_err}")
                    step(f"    skipped: {runtime_err}")
                    continue

                clone_path = repo_record.get("planner_clone_path", "")
                baseline = repo_record.get("repo_baseline_sha", "")
                if clone_path:
                    verbose_print(f"    reusing planner clone: {clone_path}")

                sub_plan = (
                    f"{plan}\n\n## Repo-specific sub-task\n\n{summary}"
                    if summary
                    else plan
                )

                try:
                    result = _run_single(
                        target_repo=target,
                        issue=issue,
                        input_prompt=input_prompt,
                        plan=sub_plan,
                        repo_record=repo_record,
                        repos_dot=repos_dot,
                        existing_repo_path=clone_path or None,
                        baseline_sha=baseline or None,
                        skip_health_checks=skip_health_checks,
                        session=session,
                        client=client,
                    )
                    updated_repos.append(result)
                    err = result.get("pr_error", "")
                    step(f"    result: {err if err else 'ok'}")
                except ExecutorError as e:
                    err_record = dict(repo_record)
                    err_record["pr_error"] = f"ExecutorError: {e}"
                    err_record["pr_skip_reason"] = f"Execution failed: {e}"
                    updated_repos.append(err_record)
                    all_errors.append(f"{target}: {e}")
                    step(f"    error: {e}")
                except RuntimeError as e:
                    err_record = dict(repo_record)
                    err_record["pr_error"] = str(e)
                    updated_repos.append(err_record)
                    all_errors.append(f"{target}: {e}")
                    step(f"    error: {e}")
        finally:
            if session is not None:
                session.stop()

        if all_errors:
            step(f"  [Executor Loop] {len(all_errors)} repo(s) had errors")

        return {"target_repos": updated_repos}


def _run_single(
    target_repo: str,
    issue: str,
    plan: str,
    repo_record: RepoRecord,
    repos_dot: str = "",
    input_prompt: str = "",
    existing_repo_path: str | None = None,
    baseline_sha: str | None = None,
    skip_health_checks: bool = False,
    session=None,
    client=None,
) -> RepoRecord:
    from agent_graph.openhands_client import OpenHandsClient

    if client is None:
        client = OpenHandsClient()
    summary = repos_dot.strip() if repos_dot else ""

    if summary:
        summary = (
            f"\n\nAffected repositories:\n{summary}"
            f"\n\nApply **all changes to this repository: {target_repo}**"
        )
    else:
        summary = f"\n\nApply changes to this repository: {target_repo}"

    full_plan = plan + summary
    result = client.run_task(
        target_repo=target_repo,
        issue=issue,
        plan=full_plan,
        existing_repo_path=existing_repo_path,
        baseline_sha=baseline_sha,
        skip_health_checks=skip_health_checks,
        input_prompt=input_prompt,
        session=session,
    )

    updated = dict(repo_record)
    updated["work_repo_path"] = result.work_repo_path or existing_repo_path or ""
    updated["repo_baseline_sha"] = result.repo_baseline_sha or baseline_sha or ""
    updated["diff_patch"] = result.diff_patch or ""
    updated["change_stat"] = result.change_stat or ""

    if result.no_changes:
        updated["pr_skip_reason"] = NO_CHANGES_SKIP
        updated["pr_error"] = ""
        if result.summary:
            updated["repo_summary"] = result.summary
    elif not result.success:
        updated["pr_error"] = result.summary or "OpenHands execution failed"
        updated["pr_skip_reason"] = result.summary or "Executor returned failure"
    else:
        updated["repo_summary"] = result.summary or updated.get("repo_summary", "")

    return updated


def _fallback_single(state: TaskState) -> dict:
    return _run_single(
        target_repo=state.get("target_repo_path", "") or state.get("github_issue_url", "") or "",
        issue=state.get("issue", ""),
        input_prompt=state.get("input_prompt", ""),
        plan=state.get("plan", ""),
        repo_record={},
    )


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
