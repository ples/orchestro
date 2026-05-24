"""Tests for OpenHandsClient helpers."""

import os
from unittest.mock import patch

from agent_graph.openhands_client import OpenHandsClient


def test_llm_base_url_for_container_rewrites_localhost():
    client = OpenHandsClient(llm_base_url="http://127.0.0.1:8555/v1")
    assert client.llm_base_url_in_container == "http://host.docker.internal:8555/v1"


def test_llm_base_url_container_override():
    with patch.dict(os.environ, {"LLM_BASE_URL_CONTAINER": "http://custom:9999/v1"}):
        client = OpenHandsClient(llm_base_url="http://127.0.0.1:8555/v1")
        assert client.llm_base_url_in_container == "http://custom:9999/v1"


def test_authenticated_git_url_injects_token():
    with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test"}):
        url = OpenHandsClient.authenticated_git_url(
            "https://github.com/ples/singer-tutor.git"
        )
        assert url == "https://x-access-token:ghp_test@github.com/ples/singer-tutor.git"


def test_authenticated_git_url_skips_non_github():
    with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test"}):
        url = OpenHandsClient.authenticated_git_url("git@github.com:org/repo.git")
        assert url == "git@github.com:org/repo.git"


def test_format_agent_error_context_limit():
    err = RuntimeError(
        "Prompt too long: 33269 tokens exceeds max context window of 32768 tokens"
    )
    msg = OpenHandsClient._format_agent_error(err)
    assert "context limit" in msg.lower()
    assert "33269" in msg


def test_build_planning_prompt_read_only():
    client = OpenHandsClient()
    prompt = client._build_planning_prompt("Fix bug", "static ctx", "/workspace/repo")
    assert "Do not modify" in prompt
    assert "## Findings" in prompt
    assert "## Implementation steps" in prompt
    assert "static ctx" in prompt


def test_build_planning_prompt_includes_developer_instructions():
    client = OpenHandsClient()
    prompt = client._build_planning_prompt(
        "Fix bug",
        "",
        "/workspace/repo",
        input_prompt="Also fix backend API",
    )
    assert "## Developer instructions" in prompt
    assert "Also fix backend API" in prompt
    assert "narrow or expand scope" in prompt


def test_build_prompt_includes_developer_instructions():
    client = OpenHandsClient()
    prompt = client._build_prompt(
        "Fix bug",
        "1. Change handler",
        "/workspace/repo",
        input_prompt="Include backend",
    )
    assert "## Developer instructions" in prompt
    assert "Include backend" in prompt
    assert "narrow or expand scope" in prompt


def test_build_prompt_includes_branch_creation_rules():
    client = OpenHandsClient()
    prompt = client._build_prompt(
        "MINSKY-1: Fix email verified\nType: Bug",
        "1. Change handler",
        "/workspace/repo",
    )
    assert "git checkout -b" in prompt
    assert "hotfix/" in prompt
    assert "feature/" in prompt
    assert "three hyphen-separated words" in prompt


def test_build_planning_prompt_omits_dev_block_when_empty():
    client = OpenHandsClient()
    prompt = client._build_planning_prompt("Fix bug", "", "/workspace/repo")
    assert "## Developer instructions" not in prompt


@patch("agent_graph.openhands_client.clone_repository")
def test_prepare_repo_skips_clone_when_existing_path(mock_clone):
    client = OpenHandsClient()
    with patch("os.path.isdir", return_value=True):
        with patch("agent_graph.openhands_client.reset_repo_to_baseline") as mock_reset:
            path, sha = client._prepare_repo(
                "https://github.com/org/repo.git",
                existing_repo_path="/tmp/existing/repo",
                baseline_sha="abc",
            )
    mock_clone.assert_not_called()
    mock_reset.assert_called_once_with("/tmp/existing/repo", "abc")
    assert path == "/tmp/existing/repo"
    assert sha == "abc"


@patch("agent_graph.openhands_client.clone_repository")
def test_prepare_repo_skips_reset_when_disabled(mock_clone):
    client = OpenHandsClient()
    with patch("os.path.isdir", return_value=True):
        with patch("agent_graph.openhands_client.reset_repo_to_baseline") as mock_reset:
            path, sha = client._prepare_repo(
                "https://github.com/org/repo.git",
                existing_repo_path="/tmp/existing/repo",
                baseline_sha="abc",
                reset_to_baseline=False,
            )
    mock_clone.assert_not_called()
    mock_reset.assert_not_called()
    assert path == "/tmp/existing/repo"
    assert sha == "abc"


def test_build_follow_up_prompt():
    client = OpenHandsClient()
    prompt = client._build_follow_up_prompt(
        "Fix UI",
        "1. Update CSS",
        "Use blue",
        "1 file changed",
        "/workspace/repo",
    )
    assert "Adjustment Plan" in prompt
    assert "Use blue" in prompt
    assert "do not revert" in prompt.lower() or "Modify the existing" in prompt
