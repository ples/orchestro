"""Tests for Bitbucket workspace repo discovery."""

from unittest.mock import MagicMock, patch

import pytest

from agent_graph.agents.bitbucket_catalog import (
    BitbucketRepoCatalog,
    _score_slug_match,
    resolve_bitbucket_repo_url,
)


class TestScoreSlugMatch:
    def test_exact_match(self):
        assert _score_slug_match("admin-ui", "admin-ui") == 1000

    def test_partial_match(self):
        assert _score_slug_match("identity-hub", "identity-hub-api") > 200

    def test_account_settings_to_account_ui(self):
        assert _score_slug_match("account-settings", "account-ui") >= 200


class TestBitbucketRepoCatalog:
    def test_resolve_slug_from_list(self):
        from agent_graph.agents.bitbucket_catalog import BitbucketRepo

        catalog = BitbucketRepoCatalog(
            workspace="dmetrics",
            auth=("user@example.com", "token"),
        )
        catalog._repos = [
            BitbucketRepo("dmetrics", "admin-ui", "dmetrics/admin-ui"),
            BitbucketRepo("dmetrics", "account-ui", "dmetrics/account-ui"),
            BitbucketRepo("dmetrics", "identity-hub-api", "dmetrics/identity-hub-api"),
        ]

        assert catalog.resolve_slug("admin-ui").slug == "admin-ui"
        assert catalog.resolve_slug("account-settings").slug == "account-ui"
        assert catalog.resolve_slug("identity-hub").slug == "identity-hub-api"

    def test_list_repos_paginates(self):
        catalog = BitbucketRepoCatalog(workspace="dmetrics", auth=("u", "t"), page_size=1)

        page1 = {"values": [{"full_name": "dmetrics/a", "slug": "a"}]}
        page2 = {"values": [{"full_name": "dmetrics/b", "slug": "b"}]}
        page3 = {"values": []}

        with patch.object(catalog, "_get", side_effect=[page1, page2, page3]) as mock_get:
            repos = catalog.list_repos(refresh=True)

        assert [r.slug for r in repos] == ["a", "b"]
        assert mock_get.call_count == 3


class TestResolveBitbucketRepoUrl:
    def test_uses_api_when_configured(self):
        mock_repo = MagicMock()
        mock_repo.clone_url = "https://bitbucket.org/dmetrics/admin-ui.git"
        mock_repo.slug = "admin-ui"

        with (
            patch.dict(
                "os.environ",
                {
                    "BB_DEFAULT_OWNER": "dmetrics",
                    "BITBUCKET_TOKEN": "tok",
                    "JIRA_EMAIL": "u@d.com",
                },
                clear=False,
            ),
            patch(
                "agent_graph.agents.bitbucket_catalog.get_catalog"
            ) as mock_catalog,
        ):
            instance = MagicMock()
            instance.available.return_value = True
            instance.resolve_slug.return_value = mock_repo
            mock_catalog.return_value = instance

            url = resolve_bitbucket_repo_url("admin-ui")

        assert url == "https://bitbucket.org/dmetrics/admin-ui.git"
