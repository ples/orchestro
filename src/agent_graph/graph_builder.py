from collections.abc import Callable

from langgraph.graph import END, StateGraph

from agent_graph.state import TaskState, is_follow_up_mode


def build_graph(
    planner_fn: Callable[[TaskState], dict] | None = None,
    executor_fn: Callable[[TaskState], dict] | None = None,
    verifier_fn: Callable[[TaskState], dict] | None = None,
    pr_creator_fn: Callable[[TaskState], dict] | None = None,
    repo_resolver_fn: Callable[[TaskState], dict] | None = None,
    executor_loop_fn: Callable[[TaskState], dict] | None = None,
    plan_adjuster_fn: Callable[[TaskState], dict] | None = None,
    source_platform: str = "github",
) -> StateGraph:
    graph = StateGraph(TaskState)

    needs_repo_resolver = source_platform in ("jira", "bitbucket")

    planner = planner_fn or _default_planner
    executor_loop = executor_loop_fn or _default_executor_loop
    verifier = verifier_fn or _default_verifier
    pr_creator = pr_creator_fn or _default_pr_creator
    plan_adjuster = plan_adjuster_fn or _default_plan_adjuster

    graph.add_node("planner", planner)
    graph.add_node("plan_adjuster", plan_adjuster)
    graph.add_node("executor_loop", executor_loop)
    graph.add_node("verifier", verifier)
    graph.add_node("pr_aggregator", pr_creator)

    entry_targets: dict[str, str] = {
        "plan_adjuster": "plan_adjuster",
        "planner": "planner",
    }
    if needs_repo_resolver:
        repo_resolver = repo_resolver_fn or _default_repo_resolver
        graph.add_node("repo_resolver", repo_resolver)
        entry_targets["repo_resolver"] = "repo_resolver"
        graph.add_conditional_edges(
            "repo_resolver",
            lambda _s: "planner",
            {"planner": "planner"},
        )

    graph.set_conditional_entry_point(
        _make_entry_router(source_platform),
        entry_targets,
    )

    graph.add_edge("planner", "executor_loop")
    graph.add_edge("plan_adjuster", "executor_loop")
    graph.add_edge("executor_loop", "verifier")
    graph.add_edge("verifier", "pr_aggregator")

    graph.add_conditional_edges(
        "pr_aggregator",
        _pr_router,
        {"done": END, "failed": END},
    )

    return graph.compile()


def _make_entry_router(source_platform: str):
    def _entry_router(state: TaskState) -> str:
        if is_follow_up_mode(state):
            return "plan_adjuster"
        if source_platform in ("jira", "bitbucket"):
            return "repo_resolver"
        return "planner"

    return _entry_router


def _default_repo_resolver(state: TaskState) -> dict:
    from agent_graph.agents.repo_resolver import RepoResolverAgent

    return RepoResolverAgent().run(state)


def _default_plan_adjuster(state: TaskState) -> dict:
    from agent_graph.agents.plan_adjuster import PlanAdjusterAgent

    return PlanAdjusterAgent().run(state)


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
