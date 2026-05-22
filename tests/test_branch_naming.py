"""Tests for branch naming conventions."""

from agent_graph.branch_naming import (
    branch_context_slug,
    branch_creation_instruction,
    branch_prefix,
    build_branch_name,
    is_agent_branch_name,
    parse_issue_type_from_text,
)


def test_branch_prefix_hotfix_for_bug_type():
    assert branch_prefix("Bug") == "hotfix"
    assert branch_prefix("Defect") == "hotfix"


def test_branch_prefix_feature_for_story():
    assert branch_prefix("Story") == "feature"
    assert branch_prefix("Epic") == "feature"


def test_build_branch_name_bug_ticket():
    issue = (
        "MINSKY-14576: Fix email verified check\n"
        "Type: Bug\n"
        "Description: users cannot log in"
    )
    assert build_branch_name(issue) == "hotfix/email-verified-check"


def test_build_branch_name_story_ticket():
    issue = "MINSKY-100: Add OAuth login support\nType: Story"
    assert build_branch_name(issue) == "feature/oauth-login-support"


def test_branch_context_slug_max_three_words():
    slug = branch_context_slug(
        "MINSKY-1: Implement very long new user registration flow redesign"
    )
    assert slug.count("-") <= 2


def test_parse_issue_type_from_text():
    assert parse_issue_type_from_text("Title\nType: Bug\nBody") == "Bug"


def test_branch_creation_instruction_includes_example():
    text = branch_creation_instruction(
        "MINSKY-1: Fix email verified\nType: Bug",
    )
    assert "git checkout -b" in text
    assert "hotfix/" in text
    assert "three hyphen-separated words" in text


def test_is_agent_branch_name():
    assert is_agent_branch_name("hotfix/email-verified-fix")
    assert is_agent_branch_name("feature/oauth-login")
    assert not is_agent_branch_name("agent/issue-42")
