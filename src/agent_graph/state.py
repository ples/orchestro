from typing import Literal, TypedDict

WorkflowNodeType = Literal[
    "idle",
    "planning",
    "executing",
    "verifying",
    "pr_creating",
    "completed",
    "rejected",
    "error",
]

SourcePlatform = Literal["github", "bitbucket", "jira"]


class RepoRecord(TypedDict, total=False):
    """Per-repo execution and PR result record."""

    target_repo_path: str
    planner_clone_path: str
    work_repo_path: str
    repo_baseline_sha: str
    diff_patch: str
    change_stat: str
    pr_url: str
    pr_error: str
    pr_skip_reason: str
    deploy_tag_name: str
    deploy_tag_error: str
    repo_summary: str


class TaskState(TypedDict, total=False):
    """Task state for the LangGraph workflow.

    All fields are optional because the graph may only update a subset
    of keys at each node.
    """

    issue: str
    input_prompt: str
    plan: str
    implementation_result: str
    verification_result: str
    # Removed: target_repo_path, work_repo_path, repo_baseline_sha,
    #          diff_patch, change_stat, pr_url, pr_error, pr_skip_reason
    #          (moved into RepoRecord below)
    target_repos: list[RepoRecord]
    source_platform: SourcePlatform
    github_issue_url: str
    jira_issue_url: str
    bitbucket_issue_url: str
    pr_url: str  # aggregated (multi-line) URL of created PRs
    pr_error: str  # aggregated (multi-line) PR errors
    pr_skip_reason: str  # aggregated (multi-line) PR skip reasons
    deploy_env: str
    deploy_tag_name: str  # aggregated deploy env tag names
    deploy_tag_error: str  # aggregated deploy tag errors
    chat_id: str
    workflow_node: WorkflowNodeType
    error_message: str
    repo_context: str
    mcp_context: str
    mcp_tools_used: list[str]


def format_agent_task(issue: str, input_prompt: str = "") -> str:
    """Issue text plus optional developer instructions for LLM agents."""
    issue = issue or ""
    prompt = (input_prompt or "").strip()
    if not prompt:
        return issue
    return (
        f"{issue}\n\n"
        "## Developer instructions (override ticket scope when they conflict)\n\n"
        f"{prompt}"
    )