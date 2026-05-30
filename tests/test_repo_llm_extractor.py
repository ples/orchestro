"""Tests for LLM-based repository selection."""

import json
from unittest.mock import patch

from agent_graph.agents.repo_detector import RepoDetectorAgent
from agent_graph.repo_llm_extractor import (
    CatalogEntry,
    _parse_extractor_response,
    _prefilter_catalog,
    catalog_entries_to_records,
    extract_target_repo_slugs,
)


CATALOG = [
    CatalogEntry(
        slug="jenkins-pipeline",
        full_name="dmetrics/jenkins-pipeline",
        clone_url="https://bitbucket.org/dmetrics/jenkins-pipeline.git",
    ),
    CatalogEntry(
        slug="admin-ui",
        full_name="dmetrics/admin-ui",
        clone_url="https://bitbucket.org/dmetrics/admin-ui.git",
    ),
    CatalogEntry(
        slug="identity-hub-api",
        full_name="dmetrics/identity-hub-api",
        clone_url="https://bitbucket.org/dmetrics/identity-hub-api.git",
    ),
]

JENKINS_ISSUE = (
    "Find jenkins-pipeline repo, general ci part, its a shared library for jenkins "
    "pipeline. I want to add an additional trigger that will trigger the build if "
    "such kind of tag created"
)


class TestParseExtractorResponse:
    def test_parses_valid_slugs(self):
        raw = json.dumps(
            {
                "repos": ["jenkins-pipeline"],
                "reason": "CI shared library task",
            }
        )
        slugs, reason = _parse_extractor_response(
            raw, {e.slug for e in CATALOG}
        )
        assert slugs == ["jenkins-pipeline"]
        assert "CI" in reason

    def test_ignores_unknown_slugs(self):
        raw = json.dumps({"repos": ["missing-repo", "admin-ui"], "reason": "x"})
        slugs, _ = _parse_extractor_response(raw, {e.slug for e in CATALOG})
        assert slugs == ["admin-ui"]

    def test_parses_json_inside_prose(self):
        payload = {"repos": ["jenkins-pipeline"], "reason": "matched"}
        raw = f"Analysis:\n{json.dumps(payload)}"
        slugs, _ = _parse_extractor_response(raw, {e.slug for e in CATALOG})
        assert slugs == ["jenkins-pipeline"]

    def test_parses_bare_json_array(self):
        slugs, _ = _parse_extractor_response(
            '["jenkins-pipeline"]', {e.slug for e in CATALOG}
        )
        assert slugs == ["jenkins-pipeline"]


class TestPrefilterCatalog:
    def test_keeps_jenkins_candidate_when_catalog_large(self):
        big = [
            CatalogEntry(f"repo-{i}", f"dmetrics/repo-{i}", f"https://bb.org/d/r{i}.git")
            for i in range(50)
        ] + [CATALOG[0]]
        picked = _prefilter_catalog(JENKINS_ISSUE, "", big, limit=10)
        slugs = {e.slug for e in picked}
        assert "jenkins-pipeline" in slugs


class TestExtractTargetRepoSlugs:
    @patch("agent_graph.repo_llm_extractor.chat_completion")
    def test_llm_selects_jenkins_pipeline(self, mock_llm):
        mock_llm.return_value = json.dumps(
            {
                "repos": ["jenkins-pipeline"],
                "reason": "shared library for jenkins pipeline CI",
            }
        )
        slugs, reason = extract_target_repo_slugs(JENKINS_ISSUE, "", CATALOG)
        assert slugs == ["jenkins-pipeline"]
        assert reason
        user_prompt = mock_llm.call_args[0][1]
        assert "jenkins-pipeline" in user_prompt
        assert "Issue text:" in user_prompt
        assert JENKINS_ISSUE[:40] in user_prompt

    @patch("agent_graph.repo_llm_extractor.chat_completion", return_value=None)
    def test_llm_unavailable_returns_empty(self, _mock_llm):
        slugs, reason = extract_target_repo_slugs(JENKINS_ISSUE, "", CATALOG)
        assert slugs == []
        assert reason == ""


class TestCatalogEntriesToRecords:
    def test_maps_to_clone_urls(self):
        records = catalog_entries_to_records(["jenkins-pipeline"], CATALOG)
        assert records == [
            {
                "target_repo_path": "https://bitbucket.org/dmetrics/jenkins-pipeline.git"
            }
        ]


class TestRepoDetectorLlmIntegration:
    @patch("agent_graph.repo_llm_extractor.chat_completion")
    @patch("agent_graph.repo_llm_extractor.list_workspace_catalog")
    def test_detector_uses_llm_when_heuristics_miss(self, mock_catalog, mock_llm):
        mock_catalog.return_value = CATALOG
        mock_llm.return_value = json.dumps(
            {"repos": ["jenkins-pipeline"], "reason": "CI library"}
        )
        state = {
            "issue": JENKINS_ISSUE,
            "input_prompt": "",
            "source_platform": "github",
            "target_repos": [],
        }
        result = RepoDetectorAgent().run(state)
        urls = [r["target_repo_path"] for r in result["target_repos"]]
        assert "https://bitbucket.org/dmetrics/jenkins-pipeline.git" in urls
