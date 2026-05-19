from enum import Enum


class AgentErrorType(Enum):
    PLANNER = "planner"
    EXECUTOR = "executor"
    VERIFIER = "verifier"


class AgentError(Exception):
    """Base exception for all agent errors."""

    def __init__(self, message: str, error_type: AgentErrorType | None = None):
        super().__init__(message)
        self.error_type = error_type


class PlannerError(AgentError):
    """Raised when planner fails."""


class ExecutorError(AgentError):
    """Raised when executor fails."""


class VerifierError(AgentError):
    """Raised when verifier fails."""


class PrCreationError(AgentError):
    """Raised when PR creation cannot proceed due to misconfiguration."""
