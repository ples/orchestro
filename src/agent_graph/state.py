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


class TaskState(TypedDict, total=False):
    """Task state for the LangGraph workflow.

    All fields are optional because the graph may only update a subset
    of keys at each node.
    """

    issue: str
    plan: str
    implementation_result: str
    verification_result: str
    target_repo_path: str
    work_repo_path: str
    repo_baseline_sha: str
    diff_patch: str
    change_stat: str
    pr_skip_reason: str
    source_platform: SourcePlatform
    github_issue_url: str
    jira_issue_url: str
    bitbucket_issue_url: str
    pr_url: str
    pr_error: str
    chat_id: str
    workflow_node: WorkflowNodeType
    error_message: str
    repo_context: str
