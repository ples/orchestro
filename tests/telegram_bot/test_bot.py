"""Tests for Telegram bot module."""

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest

from telegram_bot.bot import (
    TelegramBot,
    collect_pr_urls,
    sync_pr_url_from_repos,
    WorkflowSession,
    WORKFLOW_RUNS_BUTTON,
    TELEGRAM_SAFE_LIMIT,
    _format_plan_summary,
    _format_what_was_done,
    _run_button_label,
    _with_workflow_runs_button,
    clean_markdown_for_telegram,
    create_completed_keyboard,
    create_history_keyboard,
    create_persistent_reply_keyboard,
    create_qa_keyboard,
    create_run_detail_keyboard,
    create_run_picker_keyboard,
    create_status_keyboard,
    format_help_message,
    balance_html,
    format_task_status_message,
    split_telegram_messages,
    _html_open_tags,
)


class TestCleanMarkdownForTelegram:
    def test_escapes_html(self):
        assert clean_markdown_for_telegram("a < b & c > d") == "a &lt; b &amp; c &gt; d"

    def test_bold_and_code(self):
        assert clean_markdown_for_telegram("**bold** and `code`") == (
            "<b>bold</b> and <code>code</code>"
        )

    def test_headers(self):
        result = clean_markdown_for_telegram("## Title\n### Sub")
        assert "<b>Title</b>" in result
        assert "<b>Sub</b>" in result


class TestSplitTelegramMessages:
    def test_empty(self):
        assert split_telegram_messages("") == []

    def test_single_chunk(self):
        assert split_telegram_messages("hello") == ["hello"]

    def test_splits_long_text(self):
        text = "line\n" * 2000
        chunks = split_telegram_messages(text, limit=100)
        assert len(chunks) > 1
        assert all(len(c) <= 100 for c in chunks)

    def test_splits_without_breaking_html_tags(self):
        text = "<b>Plan</b>\n" + ("step with <code>x</code>\n" * 300)
        chunks = split_telegram_messages(text, limit=200)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 200
            assert _html_open_tags(chunk) == []


class TestBalanceHtml:
    def test_closes_unclosed_bold(self):
        assert balance_html("<b>hello") == "<b>hello</b>"


class TestFormatPlanSummary:
    def test_extracts_implementation_plan_section(self):
        plan = "intro\n## Implementation plan\n1. Do thing\n## Other\nskip"
        summary = _format_plan_summary(plan)
        assert "Implementation plan" in summary
        assert "1. Do thing" in summary

    def test_falls_back_to_global_steps(self):
        plan = "## Global implementation steps\n2. Run tests"
        summary = _format_plan_summary(plan)
        assert "Global implementation steps" in summary
        assert "Run tests" in summary

    def test_truncates_long_plan(self):
        plan = "A" * 2000
        summary = _format_plan_summary(plan)
        assert summary.endswith("…")
        assert len(summary) <= 1200

    def test_truncates_after_section_marker(self):
        plan = "## Implementation plan\n" + ("step\n" * 500)
        summary = _format_plan_summary(plan, max_chars=200)
        assert len(summary) <= 200
        assert summary.endswith("…")


class TestFormatWhatWasDone:
    def test_empty_without_repos(self):
        assert _format_what_was_done({}) == ""

    def test_formats_repo_summary_and_stat(self):
        state = {
            "target_repos": [
                {
                    "target_repo_path": "https://github.com/o/r.git",
                    "repo_summary": "## Findings\nBug fixed",
                    "change_stat": " src/a.py | 2 ++\n src/b.py | 1 +",
                }
            ]
        }
        text = _format_what_was_done(state)
        assert "github.com/o/r.git" in text
        assert "Findings" in text
        assert "src/a.py" in text

    def test_truncates_long_summary(self):
        state = {
            "target_repos": [
                {
                    "target_repo_path": "repo",
                    "repo_summary": "x" * 500,
                }
            ]
        }
        text = _format_what_was_done(state, summary_limit=100)
        assert text.endswith("…")


class TestFormatHelpMessage:
    def test_includes_commands(self):
        text = format_help_message()
        assert "/start" in text
        assert "/help" in text
        assert "/history" in text
        assert "/runs" in text

    def test_includes_task_sources_and_features(self):
        text = format_help_message()
        assert "Jira" in text
        assert "Natural language" in text
        assert "Adjust" in text

    def test_includes_natural_language_examples(self):
        text = format_help_message()
        assert "Implement OAuth" in text
        assert "PR link" in text


class TestHistoryKeyboards:
    def test_run_button_label_truncates(self):
        run = {"issue": "x" * 50, "workflow_node": "completed"}
        label = _run_button_label(run)
        assert len(label) < 60
        assert "completed" in label

    def test_history_keyboard_has_refresh(self):
        runs = [{"run_id": "abc", "issue": "Fix", "workflow_node": "done"}]
        kb = create_history_keyboard(runs)
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "view_run_abc" in callbacks
        assert "show_history" in callbacks

    def test_run_detail_keyboard_actions(self):
        kb = create_run_detail_keyboard("run-1")
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "plan_run_run-1" in callbacks
        assert "ask_run_run-1" in callbacks

    def test_qa_keyboard_has_exit(self):
        kb = create_qa_keyboard()
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "exit_qa" in callbacks

    def test_run_picker_keyboard(self):
        runs = [
            {"run_id": "run-a", "issue": "JWT task", "workflow_node": "completed"},
        ]
        kb = create_run_picker_keyboard(runs, "query")
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "pick_run_run-a_query" in callbacks
        assert "pick_run_cancel" in callbacks


class TestCreateStatusKeyboard:
    def test_keyboard_has_run_again(self):
        kb = create_status_keyboard()
        assert len(kb.inline_keyboard) == 2
        assert kb.inline_keyboard[0][0].text == "Run Again"
        assert kb.inline_keyboard[0][0].callback_data == "run_again"

    def test_keyboard_has_workflow_runs(self):
        kb = create_status_keyboard()
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert "show_history" in callbacks

    def test_persistent_reply_keyboard(self):
        kb = create_persistent_reply_keyboard()
        assert kb.keyboard[0][0].text == WORKFLOW_RUNS_BUTTON

    def test_with_workflow_runs_button_skips_duplicate(self):
        kb = create_history_keyboard(
            [{"run_id": "abc", "issue": "Fix", "workflow_node": "done"}]
        )
        assert kb is _with_workflow_runs_button(kb)


class TestFormatTaskStatusMessage:
    def test_idle_state(self):
        session = WorkflowSession(
            chat_id=123,
            state={"issue": "", "workflow_node": "idle"},
        )
        text, kb, details = format_task_status_message(session)
        assert "Ready" in text
        assert details == []

    def test_planning_state_with_repos(self):
        session = WorkflowSession(
            chat_id=123,
            state={
                "issue": "Test",
                "workflow_node": "planning",
                "target_repos": [{"target_repo_path": "https://github.com/o/r.git"}],
            },
        )
        text, kb, details = format_task_status_message(session)
        assert "Planning" in text
        assert "github.com/o/r.git" in text
        assert details == []

    def test_executing_state_with_plan(self):
        session = WorkflowSession(
            chat_id=123,
            state={
                "issue": "Test",
                "workflow_node": "executing",
                "plan": "## Implementation plan\nStep 1\nStep 2",
            },
        )
        text, kb, details = format_task_status_message(session)
        assert "Executing" in text
        assert "<b>Plan:</b>" not in text
        assert any("Step 1" in d for d in details)

    def test_executing_state_without_plan(self):
        session = WorkflowSession(
            chat_id=123,
            state={"issue": "Test", "workflow_node": "executing"},
        )
        text, kb, details = format_task_status_message(session)
        assert "Executing" in text
        assert details == []

    def test_verifying_state_shows_plan_and_changes(self):
        session = WorkflowSession(
            chat_id=123,
            state={
                "issue": "Test",
                "workflow_node": "verifying",
                "plan": "## Implementation plan\nDo work",
                "target_repos": [
                    {
                        "target_repo_path": "repo",
                        "change_stat": " file.py | 1 +",
                    }
                ],
            },
        )
        text, kb, details = format_task_status_message(session)
        assert "Verifying" in text
        assert "Do work" in " ".join(details)
        assert "file.py" in " ".join(details)
        assert len(text) < TELEGRAM_SAFE_LIMIT

    def test_pr_creating_state(self):
        session = WorkflowSession(
            chat_id=123,
            state={"workflow_node": "pr_creating"},
        )
        text, kb, details = format_task_status_message(session)
        assert "pull request" in text.lower()

    def test_collect_pr_urls_from_target_repos(self):
        state = {
            "pr_url": "",
            "target_repos": [
                {"pr_url": "https://github.com/o/r/pull/1"},
                {"pr_url": "https://github.com/o/other/pull/2"},
            ],
        }
        assert collect_pr_urls(state) == [
            "https://github.com/o/r/pull/1",
            "https://github.com/o/other/pull/2",
        ]
        sync_pr_url_from_repos(state)
        assert "pull/1" in state["pr_url"]
        assert "pull/2" in state["pr_url"]

    def test_completed_with_pr_url_only_in_target_repos(self):
        session = WorkflowSession(
            chat_id=123,
            state={
                "workflow_node": "completed",
                "iteration": 0,
                "target_repos": [
                    {"target_repo_path": "repo", "pr_url": "https://github.com/o/r/pull/9"},
                ],
            },
        )
        text, kb, details = format_task_status_message(session)
        assert "https://github.com/o/r/pull/9" in text
        assert 'href="https://github.com/o/r/pull/9"' in text

    def test_completed_with_pr_url_and_changes(self):
        session = WorkflowSession(
            chat_id=123,
            state={
                "workflow_node": "completed",
                "plan": "## Implementation plan\nShip fix",
                "pr_url": "https://github.com/o/r/pull/1",
                "iteration": 0,
                "target_repos": [
                    {
                        "target_repo_path": "repo",
                        "repo_summary": "Fixed bug",
                        "change_stat": " fix.py | 2 ++",
                    }
                ],
            },
        )
        text, kb, details = format_task_status_message(session)
        assert "https://github.com/o/r/pull/1" in text
        assert 'href="https://github.com/o/r/pull/1"' in text
        assert "Ship fix" in " ".join(details)
        assert "fix.py" in " ".join(details)
        assert "adjust" in text.lower()
        assert kb.inline_keyboard[0][0].callback_data == "adjust"
        assert len(text) < TELEGRAM_SAFE_LIMIT

    def test_completed_keyboard_no_adjust_after_limit(self):
        kb = create_completed_keyboard(can_adjust=False)
        labels = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "Adjust" not in labels
        assert "Run Again" in labels

    def test_completed_with_pr_error(self):
        session = WorkflowSession(
            chat_id=123,
            state={
                "workflow_node": "completed",
                "pr_error": "push failed",
            },
        )
        text, kb, details = format_task_status_message(session)
        assert "push failed" in text

    def test_completed_no_pr(self):
        session = WorkflowSession(
            chat_id=123,
            state={"workflow_node": "completed"},
        )
        text, kb, details = format_task_status_message(session)
        assert "no PR" in text.lower() or "no changes" in text.lower()

    def test_error_state_escapes_html(self):
        session = WorkflowSession(
            chat_id=123,
            state={
                "workflow_node": "error",
                "error_message": "bad <tag> & fail",
            },
        )
        text, kb, details = format_task_status_message(session)
        assert "bad &lt;tag&gt; &amp; fail" in text

    def test_unknown_state_fallback(self):
        session = WorkflowSession(
            chat_id=123,
            state={"workflow_node": "unknown"},
        )
        text, kb, details = format_task_status_message(session)
        assert "Ready" in text


class TestWorkflowSession:
    def test_default_lock_created(self):
        session = WorkflowSession(chat_id=123, state={"issue": "test"})
        assert isinstance(session.state_lock, asyncio.Lock)

    def test_defaults_applied(self):
        session = WorkflowSession(chat_id=123, state={"issue": "test"})
        assert session.current_message_ids == {}
        assert session.focused_run_id is None
        assert session.run_generation == 0
        assert session.workflow_task is None


class TestTelegramBotWorkflowGuard:
    def test_is_workflow_active(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:ABC-DEF"}):
            bot = TelegramBot()
            session = WorkflowSession(
                chat_id=1,
                state={"workflow_node": "executing"},
            )
            assert bot._is_workflow_active(session) is True
            session.state["workflow_node"] = "completed"
            assert bot._is_workflow_active(session) is False


class TestTelegramBot:
    def test_requires_token(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
                TelegramBot()

    def test_accepts_token_from_env(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:ABC-DEF"}):
            bot = TelegramBot()
            assert bot.bot_token == "123456:ABC-DEF"

    def test_accepts_token_from_param(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "env-token"}):
            bot = TelegramBot(bot_token="param-token")
            assert bot.bot_token == "param-token"

    def test_register_graph_builder(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:ABC-DEF"}):
            bot = TelegramBot()
            mock_builder = MagicMock()
            bot.register_graph_builder(mock_builder)
            assert bot.graph_builder is mock_builder

    def test_bot_property_lazy_init(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:ABC-DEF"}):
            bot = TelegramBot()
            assert bot._bot is None
            b = bot.bot
            assert b is not None
            assert bot._bot is b

    def test_dp_property_lazy_init(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:ABC-DEF"}):
            bot = TelegramBot()
            assert bot._dp is None
            dp = bot.dp
            assert dp is not None
            assert bot._dp is dp
