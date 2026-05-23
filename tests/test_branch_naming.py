"""Tests for branch naming conventions."""

import subprocess

import pytest

from agent_graph.branch_naming import (
    branch_context_slug,
    branch_creation_instruction,
    branch_exists_local,
    branch_name_candidates,
    branch_prefix,
    build_branch_name,
    checkout_work_branch,
    is_agent_branch_name,
    list_local_agent_branches,
    parse_issue_type_from_text,
    resolve_work_branch,
    ticket_key_slug,
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


def test_ticket_key_slug():
    issue = "MINSKY-14576: Fix email verification status\nType: Bug"
    assert ticket_key_slug(issue) == "minsky-14576"


def test_branch_name_candidates_includes_ticket_suffix():
    issue = "MINSKY-1: Fix thing\nType: Bug"
    names = branch_name_candidates("hotfix/email-verification-status", issue)
    assert names[0] == "hotfix/email-verification-status"
    assert "hotfix/email-verification-status-minsky-1" in names
    assert "hotfix/email-verification-status-2" in names


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


def test_checkout_work_branch_reuses_existing_branch(git_repo):
    subprocess.run(
        ["git", "checkout", "-b", "hotfix/email-verification-status"],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )
    assert branch_exists_local(git_repo, "hotfix/email-verification-status")
    checked_out = checkout_work_branch(
        str(git_repo),
        "hotfix/email-verification-status",
        issue="MINSKY-1: Fix email verification status",
    )
    assert checked_out == "hotfix/email-verification-status"
    head = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert head.stdout.strip() == "hotfix/email-verification-status"


def test_resolve_work_branch_finds_agent_branch_off_main(git_repo):
    subprocess.run(
        ["git", "checkout", "-b", "hotfix/email-verified-fix"],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "main"],
        cwd=git_repo,
        check=True,
        capture_output=True,
    )
    issue = "MINSKY-1: Fix email verified\nType: Bug"
    assert resolve_work_branch(str(git_repo), issue) == "hotfix/email-verified-fix"
    assert list_local_agent_branches(str(git_repo)) == ["hotfix/email-verified-fix"]
