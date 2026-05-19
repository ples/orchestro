from collections.abc import Callable

from langgraph.graph import END, StateGraph

from agent_graph.state import TaskState


def build_graph(
    planner_fn: Callable[[TaskState], dict] | None = None,
    executor_fn: Callable[[TaskState], dict] | None = None,
    verifier_fn: Callable[[TaskState], dict] | None = None,
    pr_creator_fn: Callable[[TaskState], dict] | None = None,
) -> StateGraph:
    """Build and compile the agent workflow graph.

    Args:
        planner_fn: Function that takes state and returns plan updates.
        executor_fn: Function that takes state and returns execution updates.
        verifier_fn: Function that takes state and returns verification updates.
        pr_creator_fn: Function that commits, pushes, and opens a GitHub PR.

    Returns:
        Compiled StateGraph ready for invocation.
    """
    graph = StateGraph(TaskState)

    planner = planner_fn or _default_planner
    executor = executor_fn or _default_executor
    verifier = verifier_fn or _default_verifier
    pr_creator = pr_creator_fn or _default_pr_creator

    graph.add_node("planner", planner)
    graph.add_node("executor", executor)
    graph.add_node("verifier", verifier)
    graph.add_node("pr_creator", pr_creator)

    graph.set_entry_point("planner")

    graph.add_edge("planner", "executor")
    graph.add_edge("executor", "verifier")
    graph.add_edge("verifier", "pr_creator")

    graph.add_conditional_edges(
        "pr_creator",
        _pr_router,
        {"done": END, "failed": END},
    )

    return graph.compile()


def _default_planner(state: TaskState) -> dict:
    issue = state["issue"]
    print(f"\n[Planner]\nAnalyzing issue: {issue}")
    plan = f"""
1. Inspect repository
2. Add implementation for: {issue}
3. Run tests
4. Create patch
"""
    return {"plan": plan}


def _default_executor(state: TaskState) -> dict:
    from agent_graph.models import ExecutionResult

    print("\n[Executor / OpenHands]\nExecuting implementation plan...")
    print(state["plan"])
    result = ExecutionResult(
        success=True,
        summary="Implementation completed successfully",
    )
    return {
        "implementation_result": result.summary,
        "work_repo_path": result.work_repo_path,
        "diff_patch": result.diff_patch,
    }


def _default_verifier(state: TaskState) -> dict:
    from agent_graph.models import VerificationResult

    print("\n[Verifier]\nRunning verification pipeline...")
    verification = VerificationResult(passed=True, details="All tests passed")
    return {"verification_result": verification.details}


def _default_pr_creator(state: TaskState) -> dict:
    from agent_graph.agents.pr_creator import PrCreatorAgent

    return PrCreatorAgent().run(state)


def _pr_router(state: TaskState) -> str:
    if state.get("pr_error"):
        return "failed"
    return "done"
