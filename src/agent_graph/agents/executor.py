import logging
import os

from agent_graph.exceptions import ExecutorError
from agent_graph.state import TaskState

from .base import BaseAgent

logger = logging.getLogger(__name__)


class ExecutorAgent(BaseAgent):
    """Executes the implementation plan via OpenHands agent."""

    name = "executor"

    def _execute(self, state: TaskState) -> dict:
        from agent_graph.openhands_client import OpenHandsClient

        target_repo = state.get("target_repo_path") or os.getenv(
            "TARGET_REPO_PATH"
        ) or ""

        client = OpenHandsClient()

        result = client.run_task(
            target_repo=target_repo,
            issue=state["issue"],
            plan=state["plan"],
        )

        if not result.success:
            raise ExecutorError(f"OpenHands execution failed: {result.summary}")

        return {
            "implementation_result": result.summary,
            "work_repo_path": result.work_repo_path,
            "repo_baseline_sha": result.repo_baseline_sha,
            "diff_patch": result.diff_patch,
            "change_stat": result.change_stat,
        }
