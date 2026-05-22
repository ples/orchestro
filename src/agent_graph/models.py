from pydantic import BaseModel


class ExecutionResult(BaseModel):
    success: bool
    summary: str
    no_changes: bool = False
    work_repo_path: str = ""
    repo_baseline_sha: str = ""
    diff_patch: str = ""
    change_stat: str = ""


class PlanningResult(BaseModel):
    success: bool
    summary: str
    planner_clone_path: str = ""
    repo_baseline_sha: str = ""


class VerificationResult(BaseModel):
    passed: bool
    details: str
