"""Tests for CLI output formatting."""

from agent_graph.cli_output import format_final_result


def test_format_includes_pr_url():
    text = format_final_result(
        {
            "issue": "Add Docker",
            "implementation_result": "done",
            "pr_url": "https://github.com/o/r/pull/1",
            "pr_error": "",
        }
    )
    assert "Add Docker" in text
    assert "https://github.com/o/r/pull/1" in text
    assert "Pull request" in text


def test_format_includes_skip_reason():
    text = format_final_result(
        {
            "issue": "Add Docker",
            "pr_skip_reason": "No changes detected since workflow baseline",
        }
    )
    assert "PR creation skipped" in text
    assert "No changes detected" in text


def test_format_no_changes_shows_skip_not_failed():
    text = format_final_result(
        {
            "issue": "MINSKY-1",
            "pr_error": "",
            "pr_skip_reason": (
                "https://bitbucket.org/dmetrics/identity-hub-api.git: No changes required\n"
                "https://bitbucket.org/dmetrics/account-ui.git: No changes required"
            ),
        }
    )
    assert "PR creation skipped" in text
    assert "PR creation failed" not in text
    assert "No changes required" in text
