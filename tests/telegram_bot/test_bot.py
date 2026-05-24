"""Tests for Telegram bot module."""

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest

from telegram_bot.bot import (
    TelegramBot,
    WorkflowSession,
    create_completed_keyboard,
    create_status_keyboard,
    format_task_status_message,
)


class TestCreateStatusKeyboard:
    def test_keyboard_has_run_again(self):
        kb = create_status_keyboard()
        assert len(kb.inline_keyboard) == 1
        assert kb.inline_keyboard[0][0].text == "Run Again"
        assert kb.inline_keyboard[0][0].callback_data == "run_again"


class TestFormatTaskStatusMessage:
    def test_idle_state(self):
        session = WorkflowSession(
            chat_id=123,
            state={"issue": "", "workflow_node": "idle"},
        )
        text, kb = format_task_status_message(session)
        assert "Ready" in text

    def test_planning_state(self):
        session = WorkflowSession(
            chat_id=123,
            state={"issue": "Test", "workflow_node": "planning"},
        )
        text, kb = format_task_status_message(session)
        assert "Planning" in text

    def test_executing_state_with_plan(self):
        session = WorkflowSession(
            chat_id=123,
            state={"issue": "Test", "workflow_node": "executing", "plan": "Step 1\nStep 2"},
        )
        text, kb = format_task_status_message(session)
        assert "Executing" in text
        assert "Step 1" in text

    def test_executing_state_without_plan(self):
        session = WorkflowSession(
            chat_id=123,
            state={"issue": "Test", "workflow_node": "executing"},
        )
        text, kb = format_task_status_message(session)
        assert "Executing" in text
        assert "Step" not in text

    def test_verifying_state(self):
        session = WorkflowSession(
            chat_id=123,
            state={"issue": "Test", "workflow_node": "verifying"},
        )
        text, kb = format_task_status_message(session)
        assert "Verifying" in text

    def test_pr_creating_state(self):
        session = WorkflowSession(
            chat_id=123,
            state={"workflow_node": "pr_creating"},
        )
        text, kb = format_task_status_message(session)
        assert "pull request" in text.lower()

    def test_completed_with_pr_url(self):
        session = WorkflowSession(
            chat_id=123,
            state={
                "workflow_node": "completed",
                "pr_url": "https://github.com/o/r/pull/1",
                "iteration": 0,
            },
        )
        text, kb = format_task_status_message(session)
        assert "https://github.com/o/r/pull/1" in text
        assert "adjust" in text.lower()
        assert kb.inline_keyboard[0][0].callback_data == "adjust"

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
        text, kb = format_task_status_message(session)
        assert "push failed" in text

    def test_completed_no_pr(self):
        session = WorkflowSession(
            chat_id=123,
            state={"workflow_node": "completed"},
        )
        text, kb = format_task_status_message(session)
        assert "no PR" in text.lower() or "no changes" in text.lower()

    def test_error_state(self):
        session = WorkflowSession(
            chat_id=123,
            state={"workflow_node": "error", "error_message": "Something went wrong"},
        )
        text, kb = format_task_status_message(session)
        assert "Something went wrong" in text

    def test_unknown_state_fallback(self):
        session = WorkflowSession(
            chat_id=123,
            state={"workflow_node": "unknown"},
        )
        text, kb = format_task_status_message(session)
        assert "Ready" in text


class TestWorkflowSession:
    def test_default_lock_created(self):
        session = WorkflowSession(chat_id=123, state={"issue": "test"})
        assert isinstance(session.state_lock, asyncio.Lock)

    def test_defaults_applied(self):
        session = WorkflowSession(chat_id=123, state={"issue": "test"})
        assert session.current_message_ids == {}


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
