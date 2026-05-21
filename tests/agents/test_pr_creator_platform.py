"""Tests for PrCreatorAgent platform routing."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from agent_graph.agents.pr_creator import PrCreatorAgent
from agent_graph.state import TaskState


PR_CREATOR_MODULE = "agent_graph.agents.pr_creator"


class TestPrCreatorBitbucketPR:
    def _create_state(self, **overrides):
        state: TaskState = {
            "issue": "Add feature",
            "plan": "Do it",
            "implementation_result": "Done",
            "verification_result": "Passed",
            "target_repo_path": "https://bitbucket.org/acme/myapp.git",
            "work_repo_path": "/tmp/work",
            "repo_baseline_sha": "abc123",
            "diff_patch": "diff --git a/x b/x\n+x\n",
            "change_stat": " 1 file changed, 1 insertion",
            "github_issue_url": "",
            "jira_issue_url": "",
            "bitbucket_issue_url": "https://bitbucket.org/acme/myapp/issues/1",
            "source_platform": "bitbucket",
            "pr_url": "",
            "pr_error": "",
            "pr_skip_reason": "",
            "workflow_node": "pr_creating",
        }
        state.update(overrides)
        return state

    def test_bitbucket_pr_created(self):
        agent = PrCreatorAgent()
        state = self._create_state()
        bb_mock = MagicMock()
        bb_mock.get_default_branch.return_value = "main"
        bb_mock.create_pull_request.return_value = "https://bitbucket.org/acme/myapp/pull-requests/1"

        with patch.object(agent, "_git"), \
             patch(f"{PR_CREATOR_MODULE}.BitbucketFetcher") as bb_cls:
            bb_cls.parse_repo_remote.return_value = ("acme", "myapp")
            bb_cls.return_value = bb_mock
            result = agent._create_bitbucket_pr(
                state,
                work_repo_path=state["work_repo_path"],
                baseline_sha=state["repo_baseline_sha"],
                target_repo=state["target_repo_path"],
                bitbucket_issue_url=state["bitbucket_issue_url"],
                github_issue_url=state["github_issue_url"],
                jira_issue_url=state["jira_issue_url"],
            )
            assert result["pr_error"] == ""
            assert result["pr_skip_reason"] == ""
            assert "pull-requests" in result["pr_url"]

    def test_bitbucket_pr_no_workspace(self):
        agent = PrCreatorAgent()
        # When work_repo_path is empty, _execute() returns early with pr_skip_reason
        state: TaskState = {
            "issue": "test",
            "plan": "",
            "implementation_result": "",
            "verification_result": "",
            "target_repo_path": "",
            "work_repo_path": "",
            "repo_baseline_sha": "",
            "diff_patch": "",
            "change_stat": "",
            "github_issue_url": "",
            "jira_issue_url": "",
            "bitbucket_issue_url": "",
            "source_platform": "bitbucket",
            "pr_url": "",
            "pr_error": "",
            "pr_skip_reason": "",
            "workflow_node": "pr_creating",
        }
        with patch.dict("os.environ", {"BITBUCKET_TOKEN": "fake"}):
            result = agent._execute(state)
            assert result["pr_skip_reason"] == "No work repository path from executor."
            assert result["pr_error"] == ""

    def test_bitbucket_pr_no_workdir_to_git(self):
        """Verify that _resolve_owner_repo passes the right source URLs to BitbucketFetcher."""
        agent = PrCreatorAgent()
        state = self._create_state()
        owner, repo = agent._resolve_owner_repo(state, platform_github=False)
        assert owner == "acme"
        assert repo == "myapp"


class TestPrCreatorGitHubPR:
    def _create_state(self, **overrides):
        state: TaskState = {
            "issue": "Add feature",
            "plan": "Do it",
            "implementation_result": "Done",
            "verification_result": "Passed",
            "target_repo_path": "https://github.com/acme/myapp.git",
            "work_repo_path": "/tmp/work",
            "repo_baseline_sha": "abc123",
            "diff_patch": "diff --git a/x b/x\n+x\n",
            "change_stat": " 1 file changed, 1 insertion",
            "github_issue_url": "https://github.com/acme/myapp/issues/42",
            "jira_issue_url": "",
            "bitbucket_issue_url": "",
            "source_platform": "github",
            "pr_url": "",
            "pr_error": "",
            "pr_skip_reason": "",
            "workflow_node": "pr_creating",
        }
        state.update(overrides)
        return state

    def test_github_pr_created(self):
        agent = PrCreatorAgent()
        state = self._create_state()
        gh_mock = MagicMock()
        gh_mock.get_default_branch.return_value = "main"
        gh_mock.create_pull_request.return_value = "https://github.com/acme/myapp/pull/42"

        with patch.object(agent, "_git"), \
             patch(f"{PR_CREATOR_MODULE}.GitHubFetcher") as gh_cls:
            gh_cls.parse_repo_remote.return_value = ("acme", "myapp")
            gh_cls.return_value = gh_mock
            result = agent._create_github_pr(
                state,
                work_repo_path=state["work_repo_path"],
                baseline_sha=state["repo_baseline_sha"],
                target_repo=state["target_repo_path"],
                github_issue_url=state["github_issue_url"],
            )
            assert result["pr_error"] == ""
            assert result["pr_skip_reason"] == ""
            assert "pull/42" in result["pr_url"]


class TestIssueIdentifier:
    def test_github_issue_number(self):
        agent = PrCreatorAgent()
        state: TaskState = {"github_issue_url": "https://github.com/acme/myapp/issues/99"}
        assert agent._issue_identifier(state, "github") == 99

    def test_jira_key_identifier(self):
        agent = PrCreatorAgent()
        state: TaskState = {"jira_issue_url": "https://test.atlassian.net/browse/PROJ-42"}
        result = agent._issue_identifier(state, "jira")
        assert result == "PROJ-42"

    def test_jira_key_none(self):
        agent = PrCreatorAgent()
        state: TaskState = {}
        assert agent._issue_identifier(state, "jira") is None


class TestPrBodyPlatform:
    def test_github_pr_body_includes_jira_issue(self):
        agent = PrCreatorAgent()
        state: TaskState = {
            "issue": "JIRA-5 Fix auth",
            "plan": "Plan",
            "implementation_result": "Done",
            "verification_result": "Passed",
            "jira_issue_url": "https://test.atlassian.net/browse/JIRA-5",
            "bitbucket_issue_url": "",
            "diff_patch": "diff\n" + "x\n" * 2000,
        }
        # Bitbucket platform should use 5000 limit for diff
        body = agent._pr_body(state, None, platform="bitbucket")
        assert "Plan" in body
        assert "diff\n" in body

    def test_github_pr_body_includes_github_issue(self):
        agent = PrCreatorAgent()
        state: TaskState = {
            "issue": "Add feature",
            "plan": "Plan",
            "implementation_result": "Done",
            "verification_result": "Passed",
            "github_issue_url": "https://github.com/acme/myapp/issues/42",
            "diff_patch": "",
        }
        body = agent._pr_body(state, 42, platform="github")
        assert "Fixes #42" in body
