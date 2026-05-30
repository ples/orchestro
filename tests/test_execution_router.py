"""Tests for execution_router."""

from agent_graph.execution_router import choose_execution_mode


def test_skip_when_no_changes_required():
    mode = choose_execution_mode(
        {"requires_changes": False, "execution_profile": "simple"},
        has_resolvable_targets=True,
    )
    assert mode == "skip"


def test_constrained_for_simple_with_targets():
    mode = choose_execution_mode(
        {
            "requires_changes": True,
            "execution_profile": "simple",
        },
        has_resolvable_targets=True,
    )
    assert mode == "constrained"


def test_openhands_for_complex():
    mode = choose_execution_mode(
        {
            "requires_changes": True,
            "execution_profile": "complex",
        },
        has_resolvable_targets=True,
    )
    assert mode == "openhands"
