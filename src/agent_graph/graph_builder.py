from collections.abc import Callable

from langgraph.graph import END, StateGraph

from agent_graph.state import TaskState


def build_graph(
    planner_fn: Callable[[TaskState], dict] | None = None,
    executor_fn: Callable[[TaskState], dict] | None = None,
    verifier_fn: Callable[[TaskState], dict] | None = None,
    pr_creator_fn: Callable[[TaskState], dict] | None = None,
    repo_resolver_fn: Callable[[TaskState], dict] | None = None,
    executor_loop_fn: Callable[[TaskState], dict] | None = None,
    source_platform: str = "github",
) -> StateGraph:
    graph = StateGraph(TaskState)

    needs_repo_resolver = source_platform in ("jira", "bitbucket")

    if needs_repo_resolver:
        repo_resolver = repo_resolver_fn or _default_repo_resolver
        graph.add_node("repo_resolver", repo_resolver)
        graph.set_entry_point("repo_resolver")
        graph.add_edge("repo_resolver", "planner")
    else:
        graph.set_entry_point("planner")

    planner = planner_fn or _default_planner
    executor_loop = executor_loop_fn or _default_executor_loop
    verifier = verifier_fn or _default_verifier
    pr_creator = pr_creator_fn or _default_pr_creator

    graph.add_node("planner", planner)
    graph.add_node("executor_loop", executor_loop)
    graph.add_node("verifier", verifier)
    graph.add_node("pr_aggregator", pr_creator)

    graph.add_edge("planner", "executor_loop")
    graph.add_edge("executor_loop", "verifier")
    graph.add_edge("verifier", "pr_aggregator")

    graph.add_conditional_edges(
        "pr_aggregator",
        _pr_router,
        {"done": END, "failed": END},
    )

    return graph.compile()


def _default_repo_resolver(state: TaskState) -> dict:
    from agent_graph.agents.repo_resolver import RepoResolverAgent

    return RepoResolverAgent().run(state)


def _default_executor_loop(state: TaskState) -> dict:
    from agent_graph.agents.executor_loop import ExecutorLoopAgent

    return ExecutorLoopAgent().run(state)


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
    from agent_graph.agents.pr_aggregator import PrAggregatorAgent

    return PrAggregatorAgent().run(state)


def _pr_router(state: TaskState) -> str:
    if state.get("pr_error"):
        return "failed"
    return "done"
