"""Lightweight plan adjustment for follow-up iterations (no re-clone)."""

from agent_graph.logging_config import step, verbose_print
from agent_graph.openhands_client import OpenHandsClient
from agent_graph.state import (
    RepoRecord,
    TaskState,
    can_run_follow_up,
    format_follow_up_task,
)

from .base import BaseAgent

_DIFF_TRUNCATE = 4000
_PLAN_TRUNCATE = 12000


class PlanAdjusterAgent(BaseAgent):
    """Builds an adjustment plan from prior work and a follow-up prompt."""

    name = "plan_adjuster"

    def _execute(self, state: TaskState) -> dict:
        if not can_run_follow_up(state):
            raise RuntimeError(
                "Follow-up limit reached (max 1 adjustment per task). "
                "Start a new workflow for further changes."
            )

        follow_up = (state.get("follow_up_prompt") or "").strip()
        if not follow_up:
            raise RuntimeError("follow_up_prompt is required for follow-up mode")

        prior_plan = (state.get("plan") or "").strip()
        target_repos = list(state.get("target_repos") or [])
        if not target_repos:
            raise RuntimeError(
                "No target_repos in state; cannot run follow-up without a prior run"
            )

        for repo in target_repos:
            if not repo.get("work_repo_path") and not repo.get("planner_clone_path"):
                url = repo.get("target_repo_path", "?")
                raise RuntimeError(
                    f"No work tree for {url}; run the initial workflow first"
                )

        diff_summary = self._build_diff_summary(target_repos)
        step("\n[Plan Adjuster] building adjustment plan")
        verbose_print(f"  Follow-up: {follow_up[:120]}")

        merged = self._build_adjustment_plan(
            state.get("issue", ""),
            prior_plan,
            follow_up,
            diff_summary,
        )

        return {
            "plan": merged,
            "workflow_mode": "follow_up",
            "iteration": int(state.get("iteration") or 0),
            "pr_url": "",
            "pr_error": "",
            "pr_skip_reason": "",
            "deploy_tag_name": "",
            "deploy_tag_error": "",
            "workflow_node": "planning",
        }

    @staticmethod
    def _build_diff_summary(target_repos: list[RepoRecord]) -> str:
        parts: list[str] = []
        for repo in target_repos:
            url = repo.get("target_repo_path", "")
            stat = (repo.get("change_stat") or "").strip()
            patch = (repo.get("diff_patch") or "").strip()
            if not stat and not patch:
                continue
            header = f"### {url or 'repository'}"
            block_parts = [header]
            if stat:
                block_parts.append(f"```\n{stat[:_DIFF_TRUNCATE]}\n```")
            if patch:
                block_parts.append(
                    f"```diff\n{patch[:_DIFF_TRUNCATE]}\n```"
                )
            parts.append("\n".join(block_parts))
        return "\n\n".join(parts)

    def _build_adjustment_plan(
        self,
        issue: str,
        prior_plan: str,
        follow_up: str,
        diff_summary: str,
    ) -> str:
        task = format_follow_up_task(issue, prior_plan, follow_up, diff_summary)
        prompt = (
            f"# Task\n\n{task}\n\n"
            "# Instructions\n\n"
            "1. Produce a concise adjustment plan (numbered steps).\n"
            "2. Reference existing changes; only describe what still needs to change.\n"
            "3. Do not suggest reverting to baseline or re-cloning.\n"
            "4. End with ## Implementation steps (numbered list).\n"
        )
        client = OpenHandsClient()
        llm_plan = client._plan_with_local_llm(prompt)
        if llm_plan and llm_plan.strip():
            return llm_plan.strip()

        sections = [
            "# Follow-up adjustment plan",
            "",
            "## Prior plan",
            "",
            (prior_plan[:_PLAN_TRUNCATE] if prior_plan else "(none)"),
            "",
            "## Adjustment instructions",
            "",
            follow_up,
        ]
        if diff_summary:
            sections.extend(["", "## Changes already made", "", diff_summary])
        sections.extend(
            [
                "",
                "## Implementation steps",
                "",
                "1. Review the existing changes in the work tree.",
                "2. Apply the adjustment instructions on top of current work.",
                "3. Run relevant tests.",
                "4. Ensure git diff reflects only the requested adjustments.",
            ]
        )
        return "\n".join(sections)
