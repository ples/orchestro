"""Tests for LLM message intent classification."""

import json
from unittest.mock import patch

import pytest

from agent_graph.message_classifier import (
    _looks_like_implementation_request,
    _parse_classifier_response,
    _url_fast_path,
    classify_user_message,
    fallback_intent,
    resolve_run_reference,
)


class TestParseClassifierResponse:
    def test_parses_valid_json(self):
        raw = json.dumps(
            {
                "intent": "start_task",
                "confidence": 0.9,
                "task_text": "Add OAuth",
                "question": None,
                "adjustment": None,
                "run_id": None,
                "run_hint": None,
                "reason": "New feature request",
            }
        )
        intent = _parse_classifier_response(raw)
        assert intent is not None
        assert intent["intent"] == "start_task"
        assert intent["task_text"] == "Add OAuth"

    def test_parses_fenced_json(self):
        raw = '```json\n{"intent": "help", "confidence": 1.0, "reason": "help"}\n```'
        intent = _parse_classifier_response(raw)
        assert intent is not None
        assert intent["intent"] == "help"

    def test_rejects_invalid_intent(self):
        raw = json.dumps({"intent": "unknown", "confidence": 0.9})
        assert _parse_classifier_response(raw) is None

    def test_parses_json_embedded_in_reasoning_prose(self):
        payload = {
            "intent": "start_task",
            "confidence": 0.92,
            "task_text": "Add git tag trigger",
            "reason": "new implementation work",
        }
        raw = (
            "Here's a thinking process:\n\n"
            "1. Analyze User Message\n"
            f"{json.dumps(payload)}"
        )
        intent = _parse_classifier_response(raw)
        assert intent is not None
        assert intent["intent"] == "start_task"
        assert intent["task_text"] == "Add git tag trigger"


class TestUrlFastPath:
    def test_jira_url_is_start_task(self):
        intent = _url_fast_path(
            "https://company.atlassian.net/browse/MINSKY-123"
        )
        assert intent is not None
        assert intent["intent"] == "start_task"
        assert intent["confidence"] >= 0.9

    def test_plain_text_returns_none(self):
        assert _url_fast_path("Add JWT auth") is None


class TestClassifyUserMessage:
    def test_url_skips_llm(self):
        text = "https://github.com/o/r/issues/42"
        intent = classify_user_message(text, {"workflow_node": "idle"}, [])
        assert intent["intent"] == "start_task"

    @patch("agent_graph.message_classifier.chat_completion")
    def test_llm_start_task(self, mock_llm):
        mock_llm.return_value = json.dumps(
            {
                "intent": "start_task",
                "confidence": 0.95,
                "task_text": "Implement OAuth",
                "reason": "new work",
            }
        )
        intent = classify_user_message(
            "please implement OAuth for admin-ui",
            {"workflow_node": "idle"},
            [],
        )
        assert intent["intent"] == "start_task"
        assert intent["task_text"] == "Implement OAuth"

    @patch("agent_graph.message_classifier.chat_completion")
    def test_low_confidence_becomes_ambiguous(self, mock_llm):
        mock_llm.return_value = json.dumps(
            {
                "intent": "query_run",
                "confidence": 0.4,
                "question": "what plan?",
                "reason": "unclear",
            }
        )
        intent = classify_user_message(
            "what plan?",
            {"workflow_node": "idle"},
            [],
        )
        assert intent["intent"] == "ambiguous"

    @patch("agent_graph.message_classifier.chat_completion", return_value=None)
    def test_llm_failure_fallback(self, _mock_llm):
        intent = classify_user_message(
            "something vague",
            {"workflow_node": "idle"},
            [],
        )
        assert intent["intent"] == "ambiguous"

    @patch("agent_graph.message_classifier.chat_completion", return_value=None)
    def test_plain_text_task_without_ticket_starts_workflow(self, _mock_llm):
        text = (
            "Find jenkins-pipeline repo, general ci part, its a shared library "
            "for jenkins pipeline, there will be a special rule to deploy to "
            "certain environment if the git tag exists. I want to add an "
            "additional trigger that will trigger the build if such kind of tag created"
        )
        intent = classify_user_message(text, {"workflow_node": "idle"}, [])
        assert intent["intent"] == "start_task"
        assert intent["task_text"] == text

    @patch("agent_graph.message_classifier.chat_completion")
    def test_reasoning_model_prose_still_classifies(self, mock_llm):
        mock_llm.return_value = (
            "Here's a thinking process:\n\n"
            + json.dumps(
                {
                    "intent": "start_task",
                    "confidence": 0.9,
                    "task_text": "Add tag trigger in jenkins-pipeline",
                    "reason": "new work",
                }
            )
        )
        intent = classify_user_message(
            "Find jenkins-pipeline repo and add a git tag trigger",
            {"workflow_node": "idle"},
            [],
        )
        assert intent["intent"] == "start_task"


class TestImplementationHeuristic:
    def test_detects_jenkins_task_brief(self):
        text = (
            "Find jenkins-pipeline repo and add trigger for git tag "
            "env.prod.branch.main"
        )
        assert _looks_like_implementation_request(text)

    def test_rejects_short_vague_text(self):
        assert not _looks_like_implementation_request("something vague")

    def test_fallback_start_task_for_plain_implementation(self):
        text = "Find jenkins-pipeline repo and add a git tag build trigger"
        intent = fallback_intent(text, {"workflow_node": "idle"})
        assert intent["intent"] == "start_task"
        assert intent["task_text"] == text


class TestResolveRunReference:
    @pytest.fixture
    def runs(self):
        return [
            {
                "run_id": "aaa-111",
                "issue": "Add JWT authentication to API",
                "workflow_node": "completed",
            },
            {
                "run_id": "bbb-222",
                "issue": "Fix login button color",
                "workflow_node": "completed",
            },
        ]

    def test_resolves_by_run_id(self, runs):
        res = resolve_run_reference(run_id="aaa-111", run_hint=None, runs=runs)
        assert res["status"] == "resolved"
        assert res["run_id"] == "aaa-111"

    def test_resolves_last_run_hint(self, runs):
        res = resolve_run_reference(
            run_id=None, run_hint="last run", runs=runs
        )
        assert res["status"] == "resolved"
        assert res["run_id"] == "aaa-111"

    def test_resolves_single_fuzzy_match(self, runs):
        res = resolve_run_reference(
            run_id=None, run_hint="JWT authentication", runs=runs
        )
        assert res["status"] == "resolved"
        assert res["run_id"] == "aaa-111"

    def test_ambiguous_multiple_matches(self, runs):
        runs.append(
            {
                "run_id": "ccc-333",
                "issue": "JWT refresh token support",
                "workflow_node": "completed",
            }
        )
        res = resolve_run_reference(run_id=None, run_hint="JWT", runs=runs)
        assert res["status"] == "ambiguous"
        assert len(res["candidates"]) >= 2

    def test_not_found_empty_runs(self):
        res = resolve_run_reference(run_id=None, run_hint="JWT", runs=[])
        assert res["status"] == "not_found"
