"""Tests for TaskState helpers."""

from agent_graph.state import format_agent_task


def test_format_agent_task_returns_issue_when_no_prompt():
    assert format_agent_task("Fix bug", "") == "Fix bug"
    assert format_agent_task("Fix bug", "   ") == "Fix bug"


def test_format_agent_task_appends_developer_block():
    out = format_agent_task("MINSKY-1: UI fix", "Also fix backend")
    assert "MINSKY-1: UI fix" in out
    assert "## Developer instructions" in out
    assert "Also fix backend" in out
    assert "override ticket scope" in out
