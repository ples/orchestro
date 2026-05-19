"""Tests for AgentError exception hierarchy."""

from agent_graph.exceptions import (
    AgentError,
    AgentErrorType,
    ExecutorError,
    PlannerError,
    VerifierError,
)


def test_base_agent_error():
    err = AgentError("base error", AgentErrorType.PLANNER)
    assert str(err) == "base error"
    assert err.error_type == AgentErrorType.PLANNER


def test_planner_error_inherits():
    err = PlannerError("planner failed")
    assert isinstance(err, AgentError)
    assert err.error_type is None


def test_executor_error_inherits():
    err = ExecutorError("execution failed")
    assert isinstance(err, AgentError)


def test_verifier_error_inherits():
    err = VerifierError("verification failed")
    assert isinstance(err, AgentError)
