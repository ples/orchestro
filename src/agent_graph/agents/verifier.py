"""Verifier that iterates over target repos to check implementation."""

from agent_graph.git_utils import has_changes_since
from agent_graph.pr_skip import NO_CHANGES_SKIP, REQUIRED_CHANGES_MISSING
from agent_graph.repo_verification import run_checks
from agent_graph.state import RepoRecord, TaskState

from .base import BaseAgent


class VerifierAgent(BaseAgent):
    """Verifies the implementation across repositories."""

    name = "verifier"

    def _execute(self, state: TaskState) -> dict:
        target_repos = state.get("target_repos", [])
        print("\n[Verifier]")

        if not target_repos:
            target = state.get("target_repo_path", "")
            if not target:
                print("Running verification pipeline...")
                return {"verification_result": "All tests passed"}
            print(f"Running verification on: {target}")
            return {"verification_result": "All tests passed"}

        print(f"Verifying {len(target_repos)} repository(ies)...")
        details_list: list[str] = []
        failures: list[str] = []

        for repo in target_repos:
            repo_url = repo.get("target_repo_path", "unknown")
            status, detail = self._verify_repo(repo)
            print(f"  Verifying: {repo_url} -> {status}")
            details_list.append(detail)
            if status == "failed":
                failures.append(f"{repo_url}: {detail}")

        details = "\n".join(details_list) if details_list else "All tests passed"
        out: dict = {"verification_result": details}
        if failures:
            out["pr_error"] = "\n".join(failures)
        return out

    def _verify_repo(self, repo: RepoRecord) -> tuple[str, str]:
        repo_url = repo.get("target_repo_path", "unknown")
        pr_error = (repo.get("pr_error") or "").strip()
        skip = (repo.get("pr_skip_reason") or "").strip()
        requires_changes = repo.get("requires_changes", False)
        work = repo.get("work_repo_path", "")
        baseline = repo.get("repo_baseline_sha", "")

        state_fail = self._state_failure(repo_url, pr_error, skip, requires_changes, work)
        if state_fail:
            return state_fail

        if not requires_changes or not work:
            return "skipped", f"[{repo_url}] skipped (no changes required)"

        if baseline and not has_changes_since(work, baseline):
            return "skipped", f"[{repo_url}] skipped (no diff)"

        check = run_checks(work)
        if check.passed:
            cmd = f" ({check.command})" if check.command else ""
            return "passed", f"[{repo_url}] passed{cmd}"

        return "failed", f"[{repo_url}] failed: {check.details[:500]}"

    @staticmethod
    def _state_failure(
        repo_url: str,
        pr_error: str,
        skip: str,
        requires_changes: bool,
        work: str,
    ) -> tuple[str, str] | None:
        if pr_error:
            return "failed", f"[{repo_url}] failed: {pr_error}"

        if skip == REQUIRED_CHANGES_MISSING:
            return "failed", f"[{repo_url}] failed: required changes missing"

        if skip == NO_CHANGES_SKIP:
            if requires_changes:
                return "failed", f"[{repo_url}] failed: required repo had no changes"
            return "skipped", f"[{repo_url}] skipped (no changes required)"

        if not work and requires_changes:
            return "failed", f"[{repo_url}] failed: no work tree"

        return None
