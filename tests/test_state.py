"""Tests for TaskState helpers."""

from agent_graph.state import (
    can_run_follow_up,
    format_agent_task,
    format_follow_up_task,
    is_follow_up_mode,
)


def test_format_agent_task_returns_issue_when_no_prompt():
    assert format_agent_task("Fix bug", "") == "Fix bug"
    assert format_agent_task("Fix bug", "   ") == "Fix bug"


def test_format_agent_task_appends_developer_block():
    out = format_agent_task("MINSKY-1: UI fix", "Also fix backend")
    assert "MINSKY-1: UI fix" in out
    assert "## Developer instructions" in out
    assert "Also fix backend" in out
    assert "override ticket scope" in out


def test_format_follow_up_task_includes_sections():
    out = format_follow_up_task(
        "Fix UI",
        "1. Change button",
        "Make it blue",
        diff_summary="2 files changed",
    )
    assert "Fix UI" in out
    assert "## Prior implementation plan" in out
    assert "1. Change button" in out
    assert "## Adjustment instructions" in out
    assert "Make it blue" in out
    assert "2 files changed" in out


def test_is_follow_up_mode():
    assert is_follow_up_mode({"workflow_mode": "follow_up"})
    assert not is_follow_up_mode({"workflow_mode": "initial"})
    assert not is_follow_up_mode({})


def test_can_run_follow_up():
    assert can_run_follow_up({"iteration": 0})
    assert can_run_follow_up({})
    assert not can_run_follow_up({"iteration": 1})
