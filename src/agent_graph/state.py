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
    work_repo_path: str
    repo_baseline_sha: str
    diff_patch: str
    change_stat: str
    pr_url: str
    pr_error: str
    pr_skip_reason: str
    repo_summary: str


class TaskState(TypedDict, total=False):
    """Task state for the LangGraph workflow.

    All fields are optional because the graph may only update a subset
    of keys at each node.
    """

    issue: str
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
    chat_id: str
    workflow_node: WorkflowNodeType
    error_message: str
    repo_context: str