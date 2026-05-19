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
        url = OpenHandsClient._authenticated_git_url(
            "https://github.com/ples/singer-tutor.git"
        )
        assert url == "https://x-access-token:ghp_test@github.com/ples/singer-tutor.git"


def test_authenticated_git_url_skips_non_github():
    with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_test"}):
        url = OpenHandsClient._authenticated_git_url("git@github.com:org/repo.git")
        assert url == "git@github.com:org/repo.git"
