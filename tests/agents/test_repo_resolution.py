"""Tests for Jira/Bitbucket repo resolution from issue text."""

import os
from unittest.mock import MagicMock, patch

import pytest

from agent_graph.agents.repo_detector import (
    RepoDetectorAgent,
    _detect,
    service_slug_to_repo_url,
)
from agent_graph.state import format_agent_task
from agent_graph.agents.repo_resolver import RepoResolverAgent
from agent_graph.state import TaskState


MINSKY_14576_ISSUE = """MINSKY-14576: The email verification status does not match in Admin app and in Account settings

Components: Minsky Identity Hub

Description:
All users have the same email verification status in Admin app (`Unknown`).
Email verified status in Account Settings is correct.
"""


class TestServiceSlugToRepoUrl:
    def test_jira_uses_bitbucket_api_when_available(self):
        mock_repo = MagicMock()
        mock_repo.clone_url = "https://bitbucket.org/dmetrics/admin-ui.git"
        with (
            patch.dict(
                os.environ,
                {
                    "BB_DEFAULT_OWNER": "dmetrics",
                    "BITBUCKET_TOKEN": "tok",
                    "JIRA_EMAIL": "u@d.com",
                },
                clear=False,
            ),
            patch(
                "agent_graph.agents.bitbucket_catalog.resolve_bitbucket_repo_url",
                return_value="https://bitbucket.org/dmetrics/admin-ui.git",
            ),
        ):
            url = service_slug_to_repo_url("admin-ui", "jira")
        assert url == "https://bitbucket.org/dmetrics/admin-ui.git"

    def test_jira_falls_back_to_guessed_url_without_api(self):
        with patch.dict(os.environ, {"BB_DEFAULT_OWNER": "dmetrics"}, clear=False):
            with patch(
                "agent_graph.agents.bitbucket_catalog.resolve_bitbucket_repo_url",
                return_value=None,
            ):
                url = service_slug_to_repo_url("admin-ui", "jira")
        assert url == "https://bitbucket.org/dmetrics/admin-ui.git"

    def test_jira_explicit_map_overrides(self):
        env = {
            "BB_DEFAULT_OWNER": "dmetrics",
            "JIRA_REPO_MAP": "admin-ui=https://bitbucket.org/custom/admin-ui.git",
        }
        with patch.dict(os.environ, env, clear=False):
            url = service_slug_to_repo_url("admin-ui", "jira")
        assert url == "https://bitbucket.org/custom/admin-ui.git"

    def test_github_platform_uses_github_owner(self):
        with patch.dict(
            os.environ, {"GH_DEFAULT_OWNER": "ples", "BB_DEFAULT_OWNER": "dmetrics"},
            clear=False,
        ):
            url = service_slug_to_repo_url("admin-ui", "github")
        assert url == "https://github.com/ples/admin-ui.git"


class TestRepoDetectorAmbiguousKeywords:
    def test_detect_ignores_standalone_backend_and_api(self):
        text = (
            "Highly likely this flag does not send by a backend site API. "
            "Repositories are identity hub API and admin UI. "
            "Check the admin API endpoint."
        )
        entities = _detect(text)
        slugs = {e.name.lower() for e in entities if e.kind == "service"}
        assert "backend" not in slugs
        assert "api" not in slugs
        assert "identity-hub" in slugs
        assert "admin-ui" in slugs

    def test_detect_with_developer_prompt_no_false_api_backend(self):
        issue = MINSKY_14576_ISSUE
        prompt = (
            "Verify backend site API first. Repositories are identity hub API and admin UI."
        )
        text = format_agent_task(issue, prompt)

        def fake_resolve(slug: str, workspace: str | None = None) -> str | None:
            mapping = {
                "admin-ui": "https://bitbucket.org/dmetrics/admin-ui.git",
                "account-settings": "https://bitbucket.org/dmetrics/account-ui.git",
                "identity-hub": "https://bitbucket.org/dmetrics/identity-hub-api.git",
                "backend": "https://bitbucket.org/dmetrics/minsky-backend.git",
                "api": "https://bitbucket.org/dmetrics/consul-api.git",
            }
            return mapping.get(slug)

        env = {
            "BB_DEFAULT_OWNER": "dmetrics",
            "BITBUCKET_TOKEN": "tok",
            "JIRA_EMAIL": "u@d.com",
        }
        state: TaskState = {
            "issue": issue,
            "input_prompt": prompt,
            "source_platform": "jira",
            "target_repos": [],
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch(
                "agent_graph.agents.bitbucket_catalog.resolve_bitbucket_repo_url",
                side_effect=fake_resolve,
            ),
        ):
            result = RepoDetectorAgent().run(state)

        urls = [r["target_repo_path"] for r in result["target_repos"]]
        assert "https://bitbucket.org/dmetrics/minsky-backend.git" not in urls
        assert "https://bitbucket.org/dmetrics/consul-api.git" not in urls
        assert "https://bitbucket.org/dmetrics/identity-hub-api.git" in urls
        assert "https://bitbucket.org/dmetrics/admin-ui.git" in urls

    def test_explicit_backend_repo_phrase_still_detected(self):
        entities = _detect("Changes required in the backend repository for auth.")
        slugs = {e.name.lower() for e in entities if e.kind == "service"}
        assert "backend" in slugs


class TestRepoDetectorJira:
    def test_detects_admin_and_account_bitbucket_repos(self):
        def fake_resolve(slug: str, workspace: str | None = None) -> str | None:
            mapping = {
                "admin-ui": "https://bitbucket.org/dmetrics/admin-ui.git",
                "account-settings": "https://bitbucket.org/dmetrics/account-ui.git",
                "identity-hub": "https://bitbucket.org/dmetrics/identity-hub-api.git",
            }
            return mapping.get(slug)

        env = {
            "BB_DEFAULT_OWNER": "dmetrics",
            "BITBUCKET_TOKEN": "tok",
            "JIRA_EMAIL": "u@d.com",
        }
        state: TaskState = {
            "issue": MINSKY_14576_ISSUE,
            "source_platform": "jira",
            "target_repos": [],
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch(
                "agent_graph.agents.bitbucket_catalog.resolve_bitbucket_repo_url",
                side_effect=fake_resolve,
            ),
        ):
            result = RepoDetectorAgent().run(state)

        urls = [r["target_repo_path"] for r in result["target_repos"]]
        assert "https://bitbucket.org/dmetrics/admin-ui.git" in urls
        assert "https://bitbucket.org/dmetrics/account-ui.git" in urls
        assert "https://bitbucket.org/dmetrics/identity-hub-api.git" in urls
        assert not any("github.com" in u for u in urls)


class TestRepoResolverJira:
    def test_prefers_issue_detection_over_target_repo_path(self):
        env = {
            "BB_DEFAULT_OWNER": "dmetrics",
            "TARGET_REPO_PATH": "git@github.com:ples/singer-tutor.git",
        }
        state: TaskState = {
            "issue": MINSKY_14576_ISSUE,
            "source_platform": "jira",
            "target_repos": [],
        }
        def fake_resolve(slug: str, workspace: str | None = None) -> str | None:
            return f"https://bitbucket.org/dmetrics/{slug}.git"

        with (
            patch.dict(os.environ, env, clear=False),
            patch(
                "agent_graph.agents.bitbucket_catalog.resolve_bitbucket_repo_url",
                side_effect=fake_resolve,
            ),
        ):
            result = RepoResolverAgent().run(state)

        urls = [r["target_repo_path"] for r in result["target_repos"]]
        assert "git@github.com:ples/singer-tutor.git" not in urls
        assert any("bitbucket.org/dmetrics" in u for u in urls)

    def test_falls_back_to_jira_repo_path_when_no_detection(self):
        env = {
            "JIRA_REPO_PATH": "https://bitbucket.org/acme/identity-hub.git",
            "TARGET_REPO_PATH": "git@github.com:ples/singer-tutor.git",
        }
        state: TaskState = {
            "issue": "Update the quarterly release notes document",
            "source_platform": "jira",
            "target_repos": [],
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch(
                "agent_graph.agents.bitbucket_catalog.resolve_bitbucket_repo_url",
                return_value=None,
            ),
            patch(
                "agent_graph.repo_llm_extractor.llm_repo_detect_enabled",
                return_value=False,
            ),
        ):
            result = RepoResolverAgent().run(state)

        urls = [r["target_repo_path"] for r in result["target_repos"]]
        assert urls == ["https://bitbucket.org/acme/identity-hub.git"]
