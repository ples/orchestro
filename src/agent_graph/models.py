from pydantic import BaseModel


class ExecutionResult(BaseModel):
    success: bool
    summary: str
    work_repo_path: str = ""
    repo_baseline_sha: str = ""
    diff_patch: str = ""
    change_stat: str = ""


class VerificationResult(BaseModel):
    passed: bool
    details: str
