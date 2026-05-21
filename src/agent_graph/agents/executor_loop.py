"""Per-repo executor loop: runs OpenHands for each target repository."""

from agent_graph.exceptions import ExecutorError
from agent_graph.state import RepoRecord, TaskState

from .base import BaseAgent


class ExecutorLoopAgent(BaseAgent):
    """Executes the implementation plan across multiple repositories."""

    name = "executor_loop"

    def _execute(self, state: TaskState) -> dict:
        target_repos = state.get("target_repos", [])
        plan = state.get("plan", "")
        issue = state.get("issue", "")

        if not target_repos:
            print("\n[Executor Loop] No repos to execute. Running OpenHands with no specific repo.")
            return _fallback_single(state)

        print(f"\n[Executor Loop] Running on {len(target_repos)} repository(ies)...")

        repos_dot = _extract_repos_dot(plan, target_repos)

        updated_repos: list[RepoRecord] = []
        all_errors: list[str] = []

        for i, repo_record in enumerate(target_repos):
            target = repo_record.get("target_repo_path", "")
            summary = repo_record.get("repo_summary", "")
            if not target:
                print(f"  [Repo {i+1}/{len(target_repos)}] Skipped (no target)")
                updated_repos.append(repo_record)
                continue

            if summary and not target.startswith("https://") and not target.startswith("http://") and not target.startswith("git@"):
                print(f"  [Repo {i+1}/{len(target_repos)}] Skipped local path (no work needed): {target}")
                updated_repos.append(repo_record)
                continue

            print(f"  [Repo {i+1}/{len(target_repos)}] Target: {target}")

            if summary:
                sub_plan = f"{plan}\n\n## Repo-specific sub-task\n\n{summary}"
            else:
                sub_plan = plan

            try:
                result = _run_single(
                    target_repo=target,
                    issue=issue,
                    plan=sub_plan,
                    repo_record=repo_record,
                    repos_dot=repos_dot,
                )
                updated_repos.append(result)
                err = result.get("pr_error", "")
                print(f"    Result: {'ok' if not err else err}")
            except ExecutorError as e:
                err_record = dict(repo_record)
                err_record["pr_error"] = f"ExecutorError: {e}"
                err_record["pr_skip_reason"] = f"Execution failed: {e}"
                updated_repos.append(err_record)
                all_errors.append(f"{target}: {e}")
                print(f"    Error: {e}")
            except RuntimeError as e:
                err_record = dict(repo_record)
                err_record["pr_error"] = str(e)
                updated_repos.append(err_record)
                all_errors.append(f"{target}: {e}")
                print(f"    Error: {e}")

        if all_errors:
            print(f"  [Executor Loop] {len(all_errors)} repo(s) had errors.")

        return {"target_repos": updated_repos}


def _run_single(
    target_repo: str,
    issue: str,
    plan: str,
    repo_record: RepoRecord,
    repos_dot: str = "",
) -> RepoRecord:
    from agent_graph.openhands_client import OpenHandsClient

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
    result = client.run_task(target_repo=target_repo, issue=issue, plan=full_plan)

    updated = dict(repo_record)
    updated["work_repo_path"] = result.work_repo_path or ""
    updated["repo_baseline_sha"] = result.repo_baseline_sha or ""
    updated["diff_patch"] = result.diff_patch or ""
    updated["change_stat"] = result.change_stat or ""
    updated["repo_summary"] = summary if not result.success else ""

    if not result.success:
        updated["pr_error"] = result.summary or "OpenHands execution failed"
        updated["pr_skip_reason"] = result.summary or "Executor returned failure"
    else:
        updated["repo_summary"] = result.summary or ""

    return updated


def _fallback_single(state: TaskState) -> dict:
    return _run_single(
        target_repo=state.get("target_repo_path", "") or state.get("github_issue_url", "") or "",
        issue=state.get("issue", ""),
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
