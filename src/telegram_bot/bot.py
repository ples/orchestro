"""Telegram bot implementation using aiogram 3.x."""

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from agent_graph.state import TaskState

logger = logging.getLogger(__name__)


def _empty_task_state(chat_id: int | str) -> TaskState:
    return {
        "issue": "",
        "plan": "",
        "implementation_result": "",
        "verification_result": "",
        "target_repo_path": os.getenv("TARGET_REPO_PATH", ""),
        "work_repo_path": "",
        "repo_baseline_sha": "",
        "diff_patch": "",
        "change_stat": "",
        "github_issue_url": "",
        "jira_issue_url": "",
        "bitbucket_issue_url": "",
        "source_platform": os.getenv("SOURCE_PLATFORM", "github"),
        "pr_url": "",
        "pr_error": "",
        "pr_skip_reason": "",
        "target_repos": [],
        "chat_id": str(chat_id),
        "workflow_node": "idle",
    }


@dataclass
class WorkflowSession:
    """Tracks an active workflow session tied to a Telegram chat."""

    chat_id: int | str
    state: TaskState
    state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    current_message_ids: dict[str, int] = field(default_factory=dict)


def create_status_keyboard() -> InlineKeyboardMarkup:
    """Build inline keyboard for workflow status messages."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Run Again", callback_data="run_again")],
        ]
    )


def format_task_status_message(session: WorkflowSession) -> tuple[str, InlineKeyboardMarkup]:
    """Return a formatted status string and keyboard for a given session."""
    state = session.state
    node = state.get("workflow_node", "idle")

    if node == "idle":
        return "Ready! Send me an issue to work on.", InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Start", callback_data="start")],
            ]
        )

    if node == "planning":
        return "Planning the implementation...", create_status_keyboard()

    if node == "executing":
        lines = ["Executing the plan..."]
        if state.get("plan"):
            lines.append(f"\n\nPlan:\n{state['plan'][:500]}")
        return "\n".join(lines), create_status_keyboard()

    if node == "verifying":
        return "Verifying the implementation...", create_status_keyboard()

    if node == "pr_creating":
        return "Creating pull request...", create_status_keyboard()

    if node == "completed":
        pr_url = state.get("pr_url", "")
        pr_error = state.get("pr_error", "")
        skip = state.get("pr_skip_reason", "")
        if pr_url:
            return f"Workflow completed.\n\nPull request:\n{pr_url}", create_status_keyboard()
        if pr_error:
            return f"Workflow finished with PR error:\n{pr_error}", create_status_keyboard()
        if skip:
            return f"Workflow completed.\n\nPR skipped:\n{skip}", create_status_keyboard()
        return (
            "Workflow completed (no PR — no changes detected).",
            create_status_keyboard(),
        )

    if node == "error":
        error = state.get("error_message", "An unknown error occurred.")
        return f"Error: {error}", InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Retry", callback_data="retry")],
            ]
        )

    return (
        "Ready! Send me an issue to work on.",
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Start", callback_data="start")],
            ]
        ),
    )


class TelegramBot:
    """Main Telegram bot orchestrator."""

    def __init__(self, bot_token: str | None = None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        if not self.bot_token:
            raise ValueError(
                "TELEGRAM_BOT_TOKEN env var is required. "
                "Get a token from @BotFather on Telegram."
            )

        self.graph_builder: Callable | None = None
        self._workflow_runner: Callable | None = None
        self._sessions: dict[int | str, WorkflowSession] = {}
        self._dp: Dispatcher | None = None
        self._bot: Bot | None = None
        self._router = Router()

    def register_graph_builder(self, fn: Callable) -> None:
        """Register the callable that builds the LangGraph StateGraph."""
        self.graph_builder = fn

    def register_workflow_runner(self, fn: Callable) -> None:
        """Register the callable that runs the workflow given state."""
        self._workflow_runner = fn

    def _setup_handlers(self) -> None:
        """Wire up aiogram handlers to the router."""

        @self._router.message(Command("start"))
        async def cmd_start(message: Message) -> None:
            chat_id = message.chat.id
            if chat_id not in self._sessions:
                self._sessions[chat_id] = WorkflowSession(
                    chat_id=chat_id,
                    state=_empty_task_state(chat_id),
                )
            existing = self._sessions[chat_id]
            async with existing.state_lock:
                existing.state["workflow_node"] = "idle"
                existing.state["issue"] = ""

            await message.answer("Ready! Send me an issue to work on.")

        @self._router.message(F.text)
        async def handle_task_message(message: Message) -> None:
            chat_id = message.chat.id
            issue_text = message.text.strip()

            if not issue_text or issue_text.startswith("/"):
                return

            if chat_id not in self._sessions:
                self._sessions[chat_id] = WorkflowSession(
                    chat_id=chat_id,
                    state=_empty_task_state(chat_id),
                )

            session = self._sessions[chat_id]
            async with session.state_lock:
                session.state["issue"] = issue_text
                session.state["workflow_node"] = "planning"

            text, kb = format_task_status_message(session)
            send_msg = await message.answer(text, reply_markup=kb)
            session.current_message_ids["task"] = send_msg.message_id

            try:
                await self._run_workflow(session)
            except Exception as e:
                logger.exception("Workflow failed")
                async with session.state_lock:
                    session.state["workflow_node"] = "error"
                    session.state["error_message"] = str(e)
                await self._notify_chat(session, session.chat_id)

        @self._router.callback_query(F.data.in_(["run_again", "start", "retry"]))
        async def handle_callback(callback_query: CallbackQuery) -> None:
            data = callback_query.data
            user_id = callback_query.from_user.id

            if user_id not in self._sessions:
                await callback_query.answer("No active session. Send /start first.")
                return

            session = self._sessions[user_id]

            if data in ("run_again", "start"):
                await self._handle_new_task(callback_query, session)
            elif data == "retry":
                await self._run_workflow(session)

            await callback_query.answer()

    async def _handle_new_task(self, callback_query: CallbackQuery, session: WorkflowSession) -> None:
        async with session.state_lock:
            session.state.update(_empty_task_state(session.chat_id))

        await callback_query.message.answer("Ready! Send me an issue to work on.")

    async def _run_workflow(self, session: WorkflowSession) -> None:
        await session.state_lock.acquire()
        state = dict(session.state)
        session.state_lock.release()

        state["workflow_node"] = "planning"
        await self._notify_chat(session, session.chat_id)

        if not self.graph_builder:
            raise RuntimeError("No graph builder registered")

        graph_fn = self.graph_builder()

        state["workflow_node"] = "executing"
        await self._notify_chat(session, session.chat_id)

        if hasattr(self, "_workflow_runner") and self._workflow_runner:
            result = await self._workflow_runner(state)
        else:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, lambda: graph_fn.invoke(state))

        async with session.state_lock:
            session.state.update(result)

        await self._notify_chat(session, session.chat_id)

    async def _notify_chat(self, session: WorkflowSession, chat_id: int | str) -> None:
        text, kb = format_task_status_message(session)

        msg_id = session.current_message_ids.get("task")
        if msg_id:
            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=text,
                    reply_markup=kb,
                )
            except Exception:
                msg = await self.bot.send_message(chat_id, text, reply_markup=kb)
                session.current_message_ids["task"] = msg.message_id
        else:
            msg = await self.bot.send_message(chat_id, text, reply_markup=kb)
            session.current_message_ids["task"] = msg.message_id

    @property
    def bot(self) -> Bot:
        if not self._bot:
            self._bot = Bot(
                token=self.bot_token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
        return self._bot

    @property
    def dp(self) -> Dispatcher:
        if not self._dp:
            self._dp = Dispatcher()
            self._dp.include_routers(self._router)
        return self._dp

    async def start_polling(self, limit: int = 100) -> None:
        """Start the bot in polling mode."""
        logger.info("Telegram bot started in polling mode")
        self._setup_handlers()
        self.dp.include_routers(self._router)

        bot = self.bot

        await self.dp.start_polling(
            bot,
            limit=limit,
        )

    async def stop_polling(self) -> None:
        """Stop bot polling."""
        if self.dp:
            await self.dp.stop_polling()

    async def cleanup(self) -> None:
        """Cleanup bot resources."""
        if self._bot:
            await self._bot.session.close()
