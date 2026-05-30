"""Telegram bot implementation using aiogram 3.x."""

import asyncio
import html
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from agent_graph.database import RunDatabase, get_db, load_run_state, new_run_id
from agent_graph.deploy_env import resolve_deploy_env_with_source
from agent_graph.message_classifier import (
    ACTIVE_WORKFLOW_NODES,
    MessageIntent,
    classify_user_message,
    resolve_run_reference,
)
from agent_graph.state import TaskState, can_run_follow_up

logger = logging.getLogger(__name__)

WORKFLOW_RUNS_BUTTON = "Workflow Runs"

TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_SAFE_LIMIT = 4000
PLAN_DETAIL_LIMIT = 1200
CHANGES_SUMMARY_LIMIT = 400
CHANGES_DETAIL_LIMIT = 800


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
        "pr_push_mode": "",
        "deploy_env": "",
        "deploy_env_source": "",
        "target_repos": [],
        "chat_id": str(chat_id),
        "workflow_node": "idle",
        "workflow_mode": "initial",
        "follow_up_prompt": "",
        "iteration": 0,
    }


@dataclass
class WorkflowSession:
    """Tracks an active workflow session tied to a Telegram chat."""

    chat_id: int | str
    state: TaskState
    state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    current_message_ids: dict[str, int] = field(default_factory=dict)
    detail_message_ids: list[int] = field(default_factory=list)
    focused_run_id: str | None = None
    workflow_task: asyncio.Task | None = None
    run_generation: int = 0
    pending_action: str | None = None
    pending_text: str | None = None


def create_persistent_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=WORKFLOW_RUNS_BUTTON)]],
        resize_keyboard=True,
        is_persistent=True,
    )


def _with_workflow_runs_button(
    markup: InlineKeyboardMarkup,
) -> InlineKeyboardMarkup:
    rows = [list(row) for row in markup.inline_keyboard]
    if any(
        btn.callback_data == "show_history"
        for row in rows
        for btn in row
    ):
        return markup
    rows.append(
        [InlineKeyboardButton(text=WORKFLOW_RUNS_BUTTON, callback_data="show_history")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def create_status_keyboard() -> InlineKeyboardMarkup:
    """Build inline keyboard for workflow status messages."""
    return _with_workflow_runs_button(
        InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Run Again", callback_data="run_again")],
            ]
        )
    )


_TELEGRAM_HTML_TAG_RE = re.compile(
    r"<(/?)(b|i|u|s|code|pre|a)(?:\s[^>]*)?>",
    re.IGNORECASE,
)


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def collect_pr_urls(state: TaskState) -> list[str]:
    """Aggregate PR URLs from top-level state and per-repo records."""
    seen: set[str] = set()
    urls: list[str] = []
    for raw in (state.get("pr_url") or "").splitlines():
        url = raw.strip()
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    for repo in state.get("target_repos") or []:
        url = (repo.get("pr_url") or "").strip()
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def sync_pr_url_from_repos(state: TaskState) -> None:
    """Ensure top-level pr_url is populated when only repo records have URLs."""
    urls = collect_pr_urls(state)
    if urls:
        state["pr_url"] = "\n".join(urls)


def _format_pr_link_lines(
    urls: list[str],
    *,
    limit: int = 5,
    header: str = "Pull request",
    leading_blank: bool = True,
) -> list[str]:
    lines: list[str] = []
    if leading_blank:
        lines.append("")
    lines.append(f"<b>{header}:</b>")
    for url in urls[:limit]:
        escaped = html.escape(url, quote=True)
        lines.append(f'<a href="{escaped}">{html.escape(url)}</a>')
    if len(urls) > limit:
        lines.append(f"<i>…and {len(urls) - limit} more</i>")
    return lines


def _html_open_tags(text: str) -> list[str]:
    stack: list[str] = []
    for match in _TELEGRAM_HTML_TAG_RE.finditer(text):
        if match.group(1):
            tag = match.group(2).lower()
            if stack and stack[-1] == tag:
                stack.pop()
        else:
            stack.append(match.group(2).lower())
    return stack


def balance_html(text: str) -> str:
    """Close any unclosed Telegram HTML tags."""
    open_tags = _html_open_tags(text)
    if not open_tags:
        return text
    return text + "".join(f"</{tag}>" for tag in reversed(open_tags))


def _truncate_html(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return balance_html(text)
    cut = limit - 1
    while cut > 0:
        candidate = balance_html(text[:cut] + "…")
        if len(candidate) <= limit:
            return candidate
        cut -= 20
    return balance_html(_truncate(text, max(limit // 2, 1)))


def _safe_html_cut_index(text: str, limit: int) -> int:
    if len(text) <= limit:
        return len(text)
    cut = text.rfind("\n", 0, limit)
    if cut < limit // 2:
        cut = limit
    while cut > 0:
        snippet = text[:cut]
        last_lt = snippet.rfind("<")
        if last_lt != -1 and snippet.rfind(">", last_lt) < last_lt:
            cut = last_lt
            continue
        if cut > 0 and text[cut - 1] == "<":
            cut -= 1
            continue
        return cut
    return limit


def split_telegram_messages(
    text: str,
    limit: int = TELEGRAM_SAFE_LIMIT,
) -> list[str]:
    """Split HTML text into Telegram-safe chunks without breaking tags."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= limit:
        return [balance_html(text)]

    chunks: list[str] = []
    prefix = ""
    while text:
        text = f"{prefix}{text}"
        prefix = ""
        if len(text) <= limit:
            chunks.append(balance_html(text))
            break

        cut = _safe_html_cut_index(text, limit)
        open_tags = _html_open_tags(text[:cut])
        close_suffix = "".join(f"</{tag}>" for tag in reversed(open_tags))
        reopen_prefix = "".join(f"<{tag}>" for tag in open_tags)

        while cut > 0 and len(text[:cut].rstrip()) + len(close_suffix) > limit:
            cut = _safe_html_cut_index(text, cut - 1)
            open_tags = _html_open_tags(text[:cut])
            close_suffix = "".join(f"</{tag}>" for tag in reversed(open_tags))
            reopen_prefix = "".join(f"<{tag}>" for tag in open_tags)

        chunk = balance_html(text[:cut].rstrip())
        chunks.append(chunk)
        text = text[cut:].lstrip("\n")
        prefix = reopen_prefix

    return chunks


def _run_button_label(run: dict) -> str:
    issue = _truncate(run.get("issue", ""), 36)
    node = run.get("workflow_node", "?")
    return f"{issue} ({node})"


def create_history_keyboard(runs: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for run in runs:
        run_id = run["run_id"]
        rows.append(
            [
                InlineKeyboardButton(
                    text=_run_button_label(run),
                    callback_data=f"view_run_{run_id}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="Refresh", callback_data="show_history")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def create_run_detail_keyboard(run_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="View Plan", callback_data=f"plan_run_{run_id}"
                ),
                InlineKeyboardButton(
                    text="Ask Question", callback_data=f"ask_run_{run_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Back to History", callback_data="show_history"
                )
            ],
        ]
    )


def create_qa_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Exit Q&A", callback_data="exit_qa")],
            [InlineKeyboardButton(text="Back to History", callback_data="show_history")],
        ]
    )


def create_run_picker_keyboard(
    runs: list[dict],
    action: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for run in runs[:5]:
        run_id = run["run_id"]
        rows.append(
            [
                InlineKeyboardButton(
                    text=_run_button_label(run),
                    callback_data=f"pick_run_{run_id}_{action}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="Cancel", callback_data="pick_run_cancel")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def format_help_message() -> str:
    return (
        "<b>Agent Graph Bot — Help</b>\n\n"
        "<b>Commands</b>\n"
        "/start — Reset session and start a new task\n"
        "/help — Show this message\n"
        "/history — List recent runs (alias: /runs)\n\n"
        "<b>Natural language</b>\n"
        "You can write freely — the bot detects what you mean:\n"
        "• <code>Implement OAuth for admin-ui</code> — start new work\n"
        "• <code>What's the PR link for the JWT task?</code> — ask about a run\n"
        "• <code>Make the login button blue on the last task</code> — adjust a run\n"
        "• <code>show my runs</code> — list history\n\n"
        "<b>Submit a task</b>\n"
        "Send any issue as plain text or paste a Jira, GitHub, or Bitbucket URL.\n\n"
        "<b>Workflow</b>\n"
        "Planning → Executing → Verifying → Creating pull request\n\n"
        "<b>Inline buttons</b>\n"
        "• <b>Start</b> / <b>Run Again</b> — new task\n"
        "• <b>Adjust</b> — after success; asks for adjustment instructions\n"
        "• <b>Retry</b> — after an error\n\n"
        "<b>Follow-up</b>\n"
        "After completion, send changes naturally or tap <b>Adjust</b>. "
        "One adjustment pass per task.\n\n"
        "<b>History &amp; Q&amp;A</b>\n"
        "Use /history or <b>Workflow Runs</b>. Ask questions in natural language "
        "or tap <b>Ask Question</b> to focus on one run."
    )


def create_completed_keyboard(can_adjust: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if can_adjust:
        rows.append([InlineKeyboardButton(text="Adjust", callback_data="adjust")])
    rows.append([InlineKeyboardButton(text="Run Again", callback_data="run_again")])
    return _with_workflow_runs_button(InlineKeyboardMarkup(inline_keyboard=rows))


_PLAN_SECTION_MARKERS = (
    "## Implementation plan",
    "## Global implementation steps",
)


def clean_markdown_for_telegram(text: str) -> str:
    """Escape HTML and convert basic markdown to Telegram HTML."""
    if not text:
        return ""
    escaped = html.escape(text)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"^### (.+)$", r"<b>\1</b>", escaped, flags=re.MULTILINE)
    escaped = re.sub(r"^## (.+)$", r"<b>\1</b>", escaped, flags=re.MULTILINE)
    escaped = re.sub(r"^# (.+)$", r"<b>\1</b>", escaped, flags=re.MULTILINE)
    return escaped


def _format_plan_summary(plan: str, *, max_chars: int = PLAN_DETAIL_LIMIT) -> str:
    if not (plan or "").strip():
        return ""
    plan = plan.strip()
    for marker in _PLAN_SECTION_MARKERS:
        idx = plan.find(marker)
        if idx != -1:
            plan = plan[idx:]
            break
    formatted = clean_markdown_for_telegram(plan)
    if len(formatted) > max_chars:
        formatted = _truncate_html(formatted, max_chars)
    return balance_html(formatted)


def _format_what_was_done(
    state: TaskState,
    *,
    summary_limit: int = CHANGES_SUMMARY_LIMIT,
) -> str:
    repos = state.get("target_repos") or []
    if not repos:
        return ""
    blocks: list[str] = []
    for repo in repos:
        path = (repo.get("target_repo_path") or "").strip()
        if not path:
            continue
        block_lines = [f"<b>{html.escape(path)}</b>"]
        summary = (repo.get("repo_summary") or "").strip()
        if summary:
            formatted = clean_markdown_for_telegram(summary)
            if len(formatted) > summary_limit:
                formatted = _truncate_html(formatted, summary_limit)
            else:
                formatted = balance_html(formatted)
            block_lines.append(formatted)
        stat = (repo.get("change_stat") or "").strip()
        if stat:
            block_lines.append("<b>Changes:</b>")
            stat_lines = [ln.strip() for ln in stat.splitlines() if ln.strip()]
            if len(stat_lines) > 12:
                stat_lines = stat_lines[:12] + ["…"]
            for line in stat_lines:
                block_lines.append(f"<code>{html.escape(line)}</code>")
        if len(block_lines) > 1:
            blocks.append("\n".join(block_lines))
    return "\n\n".join(blocks)


def _status_detail_sections(state: TaskState, node: str) -> list[str]:
    """Build optional follow-up messages (plan, changes) for long workflow output."""
    sections: list[str] = []
    plan = (state.get("plan") or "").strip()
    if plan and node in ("executing", "verifying", "pr_creating", "completed"):
        plan_block = _format_plan_summary(plan)
        if plan_block:
            sections.append(f"<b>Plan:</b>\n{plan_block}")
    if node in ("verifying", "pr_creating", "completed"):
        limit = (
            CHANGES_SUMMARY_LIMIT
            if node in ("verifying", "pr_creating")
            else CHANGES_DETAIL_LIMIT
        )
        changes = _format_what_was_done(state, summary_limit=limit)
        if changes:
            title = (
                "<b>Changes so far:</b>"
                if node in ("verifying", "pr_creating")
                else "<b>What was done:</b>"
            )
            sections.append(f"{title}\n{changes}")
    return sections


def format_task_status_message(
    session: WorkflowSession,
) -> tuple[str, InlineKeyboardMarkup, list[str]]:
    """Return compact status text, keyboard, and optional detail message sections."""
    state = session.state
    node = state.get("workflow_node", "idle")
    details = _status_detail_sections(state, node)

    if node == "idle":
        return (
            "Ready! Send me an issue to work on.",
            _with_workflow_runs_button(
                InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="Start", callback_data="start")],
                    ]
                )
            ),
            [],
        )

    if node == "planning":
        repos = state.get("target_repos", [])
        if repos:
            repo_list = "\n".join(
                f"• <code>{html.escape(r.get('target_repo_path', ''))}</code>"
                for r in repos
                if r.get("target_repo_path")
            )
            return (
                "<b>Planning the implementation...</b>\n\n"
                f"<b>Detected repositories:</b>\n{repo_list}",
                create_status_keyboard(),
                [],
            )
        return "<b>Planning the implementation...</b>", create_status_keyboard(), []

    if node == "executing":
        lines = ["<b>Executing the plan...</b>"]
        if details:
            lines.append("\n<i>Plan details in the next message.</i>")
        return "\n".join(lines), create_status_keyboard(), details

    if node in ("verifying", "pr_creating"):
        title = (
            "<b>Verifying the implementation...</b>"
            if node == "verifying"
            else "<b>Creating pull request...</b>"
        )
        lines = [title]
        if details:
            lines.append("<i>Details in the following messages.</i>")
        return "\n".join(lines), create_status_keyboard(), details

    if node == "completed":
        sync_pr_url_from_repos(state)
        pr_urls = collect_pr_urls(state)
        pr_error = state.get("pr_error", "")
        skip = state.get("pr_skip_reason", "")
        adjust_hint = ""
        if can_run_follow_up(state):
            adjust_hint = (
                "\n\nSend a message to adjust the result, or tap <b>Adjust</b>."
            )
        kb = create_completed_keyboard(can_run_follow_up(state))
        lines = ["<b>Workflow completed.</b>"]
        if pr_urls:
            lines.extend(_format_pr_link_lines(pr_urls))
        if details:
            lines.append("<i>Plan and changes in the following messages.</i>")
        if not pr_urls and pr_error:
            errors = [e.strip() for e in pr_error.splitlines() if e.strip()]
            lines.extend(
                ["", "<b>PR error:</b>"]
                + [_truncate(html.escape(e), 200) for e in errors[:3]]
            )
        elif not pr_urls and skip:
            lines.extend(
                ["", "<b>PR skipped:</b>"]
                + [_truncate(html.escape(s), 200) for s in skip.splitlines() if s.strip()][:3]
            )
        elif not pr_urls:
            lines.append("\nNo PR — no changes detected.")
        if adjust_hint:
            lines.append(adjust_hint)
        return "\n".join(lines), kb, details

    if node == "error":
        error = html.escape(
            state.get("error_message", "An unknown error occurred.")
        )
        return (
            f"<b>Error:</b> {_truncate(error, 500)}",
            _with_workflow_runs_button(
                InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="Retry", callback_data="retry")],
                    ]
                )
            ),
            [],
        )

    if node == "cancelled":
        error = html.escape(
            state.get("error_message", "Run was cancelled.")
        )
        return (
            f"<b>Cancelled:</b> {_truncate(error, 500)}",
            _with_workflow_runs_button(
                InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="Run Again", callback_data="run_again")],
                    ]
                )
            ),
            [],
        )

    return (
        "Ready! Send me an issue to work on.",
        _with_workflow_runs_button(
            InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Start", callback_data="start")],
                ]
            )
        ),
        [],
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
        self._run_db: RunDatabase | None = None
        self._dp: Dispatcher | None = None
        self._bot: Bot | None = None
        self._router = Router()

    async def _ensure_db(self) -> RunDatabase:
        if self._run_db is None:
            self._run_db = get_db()
            await self._run_db.init()
        return self._run_db

    async def _persist_run(self, session: WorkflowSession) -> None:
        run_id = session.state.get("run_id")
        if not run_id:
            return
        db = await self._ensure_db()
        async with session.state_lock:
            await db.save_run(run_id, str(session.chat_id), dict(session.state))

    def _is_workflow_active(self, session: WorkflowSession) -> bool:
        return session.state.get("workflow_node") in ACTIVE_WORKFLOW_NODES

    async def _cancel_active_workflow(self, session: WorkflowSession) -> bool:
        if not self._is_workflow_active(session):
            return False

        async with session.state_lock:
            old_state = dict(session.state)
            old_run_id = old_state.get("run_id")

        session.run_generation += 1

        if session.workflow_task and not session.workflow_task.done():
            session.workflow_task.cancel()
            try:
                await session.workflow_task
            except (asyncio.CancelledError, Exception):
                pass
            session.workflow_task = None

        if old_run_id:
            old_state["workflow_node"] = "cancelled"
            old_state["error_message"] = "Cancelled — replaced by new task"
            db = await self._ensure_db()
            await db.save_run(old_run_id, str(session.chat_id), old_state)

        return True

    async def _prepare_issue_from_text(
        self, issue_text: str
    ) -> tuple[str, str, str, str, str]:
        from agent_graph.main import (
            classify_input,
            _fetch_jira_issue_by_key,
            _jira_use_mcp,
        )

        classification = classify_input(issue_text)
        source_platform = "github"
        jira_issue_url = ""
        github_issue_url = ""
        bitbucket_issue_url = ""

        if classification["type"] == "jira_url":
            source_platform = "jira"
            jira_issue_key = classification["issue_key"]
            use_mcp = _jira_use_mcp(False)
            try:
                fetched_text, jira_url = _fetch_jira_issue_by_key(
                    jira_issue_key, use_mcp=use_mcp
                )
                jira_issue_url = jira_url
                issue_text = f"{fetched_text}\n\n[User Context]: {issue_text}"
            except Exception:
                logger.exception("Failed to fetch Jira issue %s", jira_issue_key)
        elif classification["type"] == "github_url":
            source_platform = "github"
            github_issue_url = issue_text
        elif classification["type"] == "bitbucket_url":
            source_platform = "bitbucket"
            bitbucket_issue_url = issue_text

        return (
            issue_text,
            source_platform,
            jira_issue_url,
            github_issue_url,
            bitbucket_issue_url,
        )

    async def _launch_workflow(
        self,
        session: WorkflowSession,
        message: Message,
        *,
        is_follow_up: bool = False,
        status_prefix: str = "",
    ) -> None:
        text, kb, details = format_task_status_message(session)
        if status_prefix:
            text = f"{status_prefix}\n\n{text}"
        if is_follow_up:
            prompt = session.state.get("follow_up_prompt") or ""
            text = f"Applying adjustment...\n\n{_truncate(prompt, 200)}"
            details = []
        send_msg = await message.answer(text, reply_markup=kb)
        session.current_message_ids["task"] = send_msg.message_id
        await self._send_detail_messages(session, session.chat_id, details)

        session.workflow_task = asyncio.create_task(self._run_workflow(session))
        try:
            await session.workflow_task
        except asyncio.CancelledError:
            logger.info("Workflow task cancelled for chat %s", session.chat_id)
        except Exception as e:
            logger.exception("Workflow failed")
            async with session.state_lock:
                if session.state.get("workflow_node") not in ("cancelled",):
                    session.state["workflow_node"] = "error"
                    session.state["error_message"] = str(e)
            await self._notify_chat(session, session.chat_id)
        finally:
            session.workflow_task = None

    async def _handle_start_task(
        self,
        session: WorkflowSession,
        message: Message,
        intent: MessageIntent,
    ) -> None:
        task_text = (intent.get("task_text") or message.text or "").strip()
        replaced = await self._cancel_active_workflow(session)

        (
            issue_text,
            source_platform,
            jira_issue_url,
            github_issue_url,
            bitbucket_issue_url,
        ) = await self._prepare_issue_from_text(task_text)
        deploy_env, deploy_env_source = resolve_deploy_env_with_source(
            issue=issue_text,
            input_prompt=task_text,
            env_var=os.getenv("DEPLOY_ENV"),
        )

        async with session.state_lock:
            session.focused_run_id = None
            session.state = _empty_task_state(session.chat_id)
            session.state["issue"] = issue_text
            session.state["workflow_mode"] = "initial"
            session.state["follow_up_prompt"] = ""
            session.state["iteration"] = 0
            session.state["workflow_node"] = "planning"
            session.state["run_id"] = new_run_id()
            session.state["source_platform"] = source_platform
            session.state["jira_issue_url"] = jira_issue_url
            session.state["github_issue_url"] = github_issue_url
            session.state["bitbucket_issue_url"] = bitbucket_issue_url
            session.state["deploy_env"] = deploy_env or ""
            session.state["deploy_env_source"] = deploy_env_source

        prefix = (
            "Previous run cancelled — starting new task."
            if replaced
            else ""
        )
        await self._launch_workflow(session, message, status_prefix=prefix)

    async def _handle_query_run(
        self,
        session: WorkflowSession,
        message: Message,
        intent: MessageIntent,
        runs: list[dict],
    ) -> None:
        question = (intent.get("question") or message.text or "").strip()
        if self._is_workflow_active(session):
            current_id = session.state.get("run_id")
            if current_id:
                db = await self._ensure_db()
                run_row = await db.get_run(current_id)
                if run_row:
                    session.focused_run_id = current_id
                    await self._answer_run_question(session, message, run_row, question)
                    return

        resolution = resolve_run_reference(
            run_id=intent.get("run_id"),
            run_hint=intent.get("run_hint"),
            runs=runs,
            confidence=intent.get("confidence", 0.0),
        )
        if resolution["status"] == "resolved" and resolution["run_id"]:
            db = await self._ensure_db()
            run = await db.get_run(resolution["run_id"])
            if not run:
                await message.answer("Run not found. Try /history.")
                return
            session.focused_run_id = resolution["run_id"]
            await self._answer_run_question(session, message, run, question)
            return

        if resolution["status"] == "ambiguous":
            session.pending_action = "query"
            session.pending_text = question
            await message.answer(
                "Which run do you mean?",
                reply_markup=create_run_picker_keyboard(
                    resolution["candidates"], "query"
                ),
            )
            return

        await message.answer(
            "I couldn't find that run. Use /history to browse past runs."
        )

    async def _answer_run_question(
        self,
        session: WorkflowSession,
        message: Message,
        run: dict,
        question: str,
    ) -> None:
        db = await self._ensure_db()
        answer = await db.ask_question(run, question)
        reply = clean_markdown_for_telegram(answer)
        if len(reply) > 4000:
            reply = reply[:3999] + "…"
        await message.answer(reply, reply_markup=create_qa_keyboard())

    async def _handle_adjust_run(
        self,
        session: WorkflowSession,
        message: Message,
        intent: MessageIntent,
        runs: list[dict],
    ) -> None:
        adjustment = (intent.get("adjustment") or message.text or "").strip()

        if self._is_workflow_active(session):
            current_id = session.state.get("run_id")
            resolution = resolve_run_reference(
                run_id=intent.get("run_id") or current_id,
                run_hint=intent.get("run_hint"),
                runs=runs,
                confidence=intent.get("confidence", 0.0),
            )
            if resolution["run_id"] and resolution["run_id"] != current_id:
                await message.answer(
                    "A workflow is in progress. Wait for it to finish before "
                    "adjusting a different run, or send a new task to replace it."
                )
                return

        resolution = resolve_run_reference(
            run_id=intent.get("run_id"),
            run_hint=intent.get("run_hint"),
            runs=runs,
            confidence=intent.get("confidence", 0.0),
        )
        if resolution["status"] == "ambiguous":
            session.pending_action = "adjust"
            session.pending_text = adjustment
            await message.answer(
                "Which run should I adjust?",
                reply_markup=create_run_picker_keyboard(
                    resolution["candidates"], "adjust"
                ),
            )
            return

        if resolution["status"] != "resolved" or not resolution["run_id"]:
            await message.answer(
                "I couldn't find a run to adjust. Use /history to pick one."
            )
            return

        await self._apply_adjustment(
            session, message, resolution["run_id"], adjustment
        )

    async def _apply_adjustment(
        self,
        session: WorkflowSession,
        message: Message,
        run_id: str,
        adjustment: str,
    ) -> None:
        db = await self._ensure_db()
        run = await db.get_run(run_id)
        if not run:
            await message.answer("Run not found.")
            return

        loaded = load_run_state(run)
        if loaded.get("workflow_node") != "completed":
            await message.answer(
                "That run is not completed yet — cannot adjust it."
            )
            return
        if not can_run_follow_up(loaded):
            await message.answer(
                "Follow-up limit reached for that run. Start a new task instead."
            )
            return

        if self._is_workflow_active(session):
            await self._cancel_active_workflow(session)

        async with session.state_lock:
            session.focused_run_id = None
            session.state = loaded
            session.state["chat_id"] = str(session.chat_id)
            session.state["follow_up_prompt"] = adjustment
            session.state["workflow_mode"] = "follow_up"
            session.state["workflow_node"] = "planning"

        await self._launch_workflow(session, message, is_follow_up=True)

    async def _show_history(self, message: Message) -> None:
        db = await self._ensure_db()
        runs = await db.list_runs(chat_id=str(message.chat.id), limit=10)
        if not runs:
            await message.answer(
                "No past runs found for this chat.",
                reply_markup=create_persistent_reply_keyboard(),
            )
            return
        await message.answer(
            "<b>Recent runs</b>\nTap a run for details.",
            reply_markup=create_history_keyboard(runs),
        )

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
            logger.info(f"[Telegram Bot] Received /start command from chat_id {chat_id}")
            if chat_id not in self._sessions:
                self._sessions[chat_id] = WorkflowSession(
                    chat_id=chat_id,
                    state=_empty_task_state(chat_id),
                )
            existing = self._sessions[chat_id]
            async with existing.state_lock:
                existing.state["workflow_node"] = "idle"
                existing.state["issue"] = ""

            await message.answer(
                "Ready! Send me an issue to work on. Send /help for commands and tips.",
                reply_markup=create_persistent_reply_keyboard(),
            )

        @self._router.message(Command("help"))
        async def cmd_help(message: Message) -> None:
            logger.info(
                f"[Telegram Bot] Received /help from chat_id {message.chat.id}"
            )
            await message.answer(format_help_message())

        @self._router.message(Command("history", "runs"))
        async def cmd_history(message: Message) -> None:
            chat_id = message.chat.id
            logger.info(f"[Telegram Bot] Received /history from chat_id {chat_id}")
            if chat_id not in self._sessions:
                self._sessions[chat_id] = WorkflowSession(
                    chat_id=chat_id,
                    state=_empty_task_state(chat_id),
                )
            await self._show_history(message)

        @self._router.message(F.text)
        async def handle_task_message(message: Message) -> None:
            chat_id = message.chat.id
            issue_text = message.text.strip()

            if not issue_text or issue_text.startswith("/"):
                return

            if issue_text == WORKFLOW_RUNS_BUTTON:
                await self._show_history(message)
                return

            logger.info(
                "[Telegram Bot] Received user message from chat_id %s: %r",
                chat_id,
                issue_text,
            )

            if chat_id not in self._sessions:
                self._sessions[chat_id] = WorkflowSession(
                    chat_id=chat_id,
                    state=_empty_task_state(chat_id),
                )

            session = self._sessions[chat_id]
            db = await self._ensure_db()

            if session.focused_run_id:
                run = await db.get_run(session.focused_run_id)
                if not run:
                    session.focused_run_id = None
                    await message.answer("Run not found. Q&A mode exited.")
                else:
                    await self._answer_run_question(
                        session, message, run, issue_text
                    )
                    return

            recent_runs = await db.list_runs(chat_id=str(chat_id), limit=10)
            intent = classify_user_message(
                issue_text,
                session.state,
                recent_runs,
                focused_run_id=session.focused_run_id,
            )

            if intent["intent"] == "help":
                await message.answer(format_help_message())
                return

            if intent["intent"] == "list_runs":
                await self._show_history(message)
                return

            if intent["intent"] == "start_task":
                await self._handle_start_task(session, message, intent)
                return

            if intent["intent"] == "query_run":
                await self._handle_query_run(
                    session, message, intent, recent_runs
                )
                return

            if intent["intent"] == "adjust_run":
                await self._handle_adjust_run(
                    session, message, intent, recent_runs
                )
                return

            await message.answer(
                "I'm not sure what you mean. Try:\n"
                "• Describe a task to implement\n"
                "• Ask about a past run (e.g. <i>what was the plan for …?</i>)\n"
                "• Request a change (e.g. <i>make the button blue on the last task</i>)\n"
                "• /help or /history",
            )

        @self._router.callback_query(
            F.data.in_(["run_again", "start", "retry", "adjust"])
        )
        async def handle_callback(callback_query: CallbackQuery) -> None:
            data = callback_query.data
            chat_id = callback_query.message.chat.id
            logger.info(f"[Telegram Bot] Received callback query '{data}' from chat_id {chat_id}")

            if chat_id not in self._sessions:
                await callback_query.answer("No active session. Send /start first.")
                return

            session = self._sessions[chat_id]

            if data in ("run_again", "start"):
                await self._handle_new_task(callback_query, session)
            elif data == "adjust":
                if not can_run_follow_up(session.state):
                    await callback_query.answer(
                        "Follow-up limit reached. Tap Run Again for a new task."
                    )
                    return
                await callback_query.message.answer(
                    "Send your adjustment instructions as a message."
                )
            elif data == "retry":
                session.workflow_task = asyncio.create_task(
                    self._run_workflow(session)
                )
                try:
                    await session.workflow_task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.exception("Workflow retry failed")
                    async with session.state_lock:
                        session.state["workflow_node"] = "error"
                        session.state["error_message"] = str(e)
                    await self._notify_chat(session, session.chat_id)
                finally:
                    session.workflow_task = None

            await callback_query.answer()

        @self._router.callback_query(F.data.startswith("view_run_"))
        async def handle_view_run(callback_query: CallbackQuery) -> None:
            run_id = callback_query.data.removeprefix("view_run_")
            db = await self._ensure_db()
            run = await db.get_run(run_id)
            if not run:
                await callback_query.answer("Run not found.")
                return
            issue = clean_markdown_for_telegram(_truncate(run.get("issue", ""), 500))
            lines = [
                f"<b>Run</b> <code>{html.escape(run_id[:8])}…</code>",
                f"<b>Status:</b> {html.escape(run.get('workflow_node', ''))}",
                f"<b>Created:</b> {html.escape(run.get('created_at', ''))}",
            ]
            if issue:
                lines.extend(["", "<b>Issue:</b>", issue])
            run_state = load_run_state(run)
            sync_pr_url_from_repos(run_state)
            pr_urls = collect_pr_urls(run_state)
            if pr_urls:
                lines.extend(
                    _format_pr_link_lines(pr_urls, header="PR", leading_blank=True)
                )
            summary = "\n".join(lines)
            plan_preview = _format_plan_summary(run.get("plan", ""), max_chars=600)
            parts = [summary]
            if plan_preview:
                parts.append(f"<b>Plan preview:</b>\n{plan_preview}")
            for i, part in enumerate(parts):
                chunks = split_telegram_messages(part)
                for j, chunk in enumerate(chunks):
                    is_last = i == len(parts) - 1 and j == len(chunks) - 1
                    await callback_query.message.answer(
                        chunk,
                        reply_markup=create_run_detail_keyboard(run_id) if is_last else None,
                    )
            await callback_query.answer()

        @self._router.callback_query(F.data.startswith("plan_run_"))
        async def handle_plan_run(callback_query: CallbackQuery) -> None:
            run_id = callback_query.data.removeprefix("plan_run_")
            db = await self._ensure_db()
            run = await db.get_run(run_id)
            if not run:
                await callback_query.answer("Run not found.")
                return
            plan = (run.get("plan") or "").strip()
            if not plan:
                await callback_query.message.answer("No plan stored for this run.")
            else:
                header = (
                    f"<b>Plan for run</b> <code>{html.escape(run_id[:8])}…</code>"
                )
                body = _format_plan_summary(plan, max_chars=TELEGRAM_SAFE_LIMIT)
                parts = split_telegram_messages(f"{header}\n\n{body}")
                for i, part in enumerate(parts):
                    markup = create_run_detail_keyboard(run_id) if i == len(parts) - 1 else None
                    await callback_query.message.answer(part, reply_markup=markup)
            await callback_query.answer()

        @self._router.callback_query(F.data.startswith("ask_run_"))
        async def handle_ask_run(callback_query: CallbackQuery) -> None:
            run_id = callback_query.data.removeprefix("ask_run_")
            chat_id = callback_query.message.chat.id
            db = await self._ensure_db()
            run = await db.get_run(run_id)
            if not run:
                await callback_query.answer("Run not found.")
                return
            if chat_id not in self._sessions:
                self._sessions[chat_id] = WorkflowSession(
                    chat_id=chat_id,
                    state=_empty_task_state(chat_id),
                )
            session = self._sessions[chat_id]
            session.focused_run_id = run_id
            await callback_query.message.answer(
                "Q&A mode active. Send your question as a message.",
                reply_markup=create_qa_keyboard(),
            )
            await callback_query.answer()

        @self._router.callback_query(F.data == "exit_qa")
        async def handle_exit_qa(callback_query: CallbackQuery) -> None:
            chat_id = callback_query.message.chat.id
            session = self._sessions.get(chat_id)
            if session:
                session.focused_run_id = None
            await callback_query.message.answer("Q&A mode exited.")
            await callback_query.answer()

        @self._router.callback_query(F.data == "show_history")
        async def handle_show_history(callback_query: CallbackQuery) -> None:
            await self._show_history(callback_query.message)
            await callback_query.answer()

        @self._router.callback_query(F.data.startswith("pick_run_"))
        async def handle_pick_run(callback_query: CallbackQuery) -> None:
            data = callback_query.data.removeprefix("pick_run_")
            if data == "cancel":
                chat_id = callback_query.message.chat.id
                session = self._sessions.get(chat_id)
                if session:
                    session.pending_action = None
                    session.pending_text = None
                await callback_query.answer("Cancelled.")
                return

            chat_id = callback_query.message.chat.id
            if chat_id not in self._sessions:
                await callback_query.answer("No active session.")
                return

            session = self._sessions[chat_id]
            if data.endswith("_query"):
                run_id = data[: -len("_query")]
                action = "query"
            elif data.endswith("_adjust"):
                run_id = data[: -len("_adjust")]
                action = "adjust"
            else:
                await callback_query.answer("Invalid selection.")
                return

            pending_text = session.pending_text or ""
            session.pending_action = None
            session.pending_text = None

            if action == "query":
                db = await self._ensure_db()
                run = await db.get_run(run_id)
                if not run:
                    await callback_query.answer("Run not found.")
                    return
                session.focused_run_id = run_id
                question = pending_text or "Summarize this run."
                await self._answer_run_question(
                    session, callback_query.message, run, question
                )
            else:
                adjustment = pending_text or "Apply the requested changes."
                await self._apply_adjustment(
                    session,
                    callback_query.message,
                    run_id,
                    adjustment,
                )

            await callback_query.answer()

    async def _handle_new_task(self, callback_query: CallbackQuery, session: WorkflowSession) -> None:
        if self._is_workflow_active(session):
            await self._cancel_active_workflow(session)
        async with session.state_lock:
            session.state.update(_empty_task_state(session.chat_id))
        session.focused_run_id = None
        session.pending_action = None
        session.pending_text = None

        await callback_query.message.answer(
            "Ready! Send me an issue to work on.",
            reply_markup=create_persistent_reply_keyboard(),
        )

    async def _handle_node_update(
        self,
        session: WorkflowSession,
        node_name: str,
        node_update: dict,
        generation: int,
    ) -> None:
        if generation != session.run_generation:
            return
        async with session.state_lock:
            session.state.update(node_update)
            if node_name == "repo_resolver":
                session.state["workflow_node"] = "planning"
            elif node_name == "planner":
                session.state["workflow_node"] = "executing"
            elif node_name == "executor_loop":
                session.state["workflow_node"] = "verifying"
            elif node_name == "verifier":
                session.state["workflow_node"] = "pr_creating"
            elif node_name == "pr_aggregator":
                sync_pr_url_from_repos(session.state)
                session.state["workflow_node"] = "completed"

        await self._notify_chat(session, session.chat_id)
        await self._persist_run(session)

    async def _run_workflow(self, session: WorkflowSession) -> None:
        async with session.state_lock:
            generation = session.run_generation
            state = dict(session.state)

        if not self.graph_builder:
            raise RuntimeError("No graph builder registered")

        async with session.state_lock:
            if not state.get("run_id"):
                state["run_id"] = new_run_id()
                session.state["run_id"] = state["run_id"]
        await self._persist_run(session)

        graph_fn = self.graph_builder()

        if hasattr(self, "_workflow_runner") and self._workflow_runner:
            result = await self._workflow_runner(state)
            if generation != session.run_generation:
                return
        else:
            loop = asyncio.get_event_loop()

            def run_stream():
                final = state
                config = {"configurable": {"thread_id": state.get("run_id", "")}}
                for event in graph_fn.stream(state, config=config, stream_mode="updates"):
                    if generation != session.run_generation:
                        break
                    for node_name, node_update in event.items():
                        if generation != session.run_generation:
                            return final
                        fut = asyncio.run_coroutine_threadsafe(
                            self._handle_node_update(
                                session, node_name, node_update, generation
                            ),
                            loop,
                        )
                        fut.result()
                        final = {**final, **node_update}
                return final

            result = await loop.run_in_executor(None, run_stream)

        if generation != session.run_generation:
            return

        async with session.state_lock:
            if generation != session.run_generation:
                return
            session.state.update(result)
            sync_pr_url_from_repos(session.state)
            if session.state.get("workflow_node") not in ("cancelled", "error"):
                session.state["workflow_node"] = "completed"

        await self._persist_run(session)
        await self._notify_chat(session, session.chat_id)

    async def _clear_detail_messages(
        self, session: WorkflowSession, chat_id: int | str
    ) -> None:
        for msg_id in session.detail_message_ids:
            try:
                await self.bot.delete_message(chat_id=chat_id, message_id=msg_id)
            except Exception:
                pass
        session.detail_message_ids.clear()

    async def _send_detail_messages(
        self,
        session: WorkflowSession,
        chat_id: int | str,
        sections: list[str],
    ) -> None:
        if not sections:
            return
        for section in sections:
            for chunk in split_telegram_messages(section):
                chunk = balance_html(chunk)
                if len(chunk) > TELEGRAM_MESSAGE_LIMIT:
                    chunk = _truncate_html(chunk, TELEGRAM_SAFE_LIMIT)
                msg = await self.bot.send_message(chat_id, chunk)
                session.detail_message_ids.append(msg.message_id)

    async def _send_pr_link_message(
        self,
        chat_id: int | str,
        urls: list[str],
        message_id: int | None = None,
    ) -> int | None:
        if not urls:
            return message_id
        text = balance_html("\n".join(_format_pr_link_lines(urls)))
        if message_id:
            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                )
                return message_id
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc).lower():
                    return message_id
            except Exception:
                logger.warning(
                    "Failed to edit PR message for chat %s, sending new message",
                    chat_id,
                    exc_info=True,
                )
        msg = await self.bot.send_message(chat_id, text)
        return msg.message_id

    async def _notify_chat(self, session: WorkflowSession, chat_id: int | str) -> None:
        text, kb, details = format_task_status_message(session)
        text = balance_html(text)
        if len(text) > TELEGRAM_SAFE_LIMIT:
            text = _truncate_html(text, TELEGRAM_SAFE_LIMIT)

        await self._clear_detail_messages(session, chat_id)

        pr_urls = (
            collect_pr_urls(session.state)
            if session.state.get("workflow_node") == "completed"
            else []
        )

        msg_id = session.current_message_ids.get("task")
        status_sent = False
        if msg_id:
            try:
                await self.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=text,
                    reply_markup=kb,
                )
                status_sent = True
            except TelegramBadRequest as exc:
                if "message is not modified" in str(exc).lower():
                    status_sent = True
                else:
                    logger.warning(
                        "Failed to edit status message for chat %s, sending new message",
                        chat_id,
                        exc_info=True,
                    )
            except Exception:
                logger.warning(
                    "Failed to edit status message for chat %s, sending new message",
                    chat_id,
                    exc_info=True,
                )
        if not status_sent:
            try:
                msg = await self.bot.send_message(chat_id, text, reply_markup=kb)
                session.current_message_ids["task"] = msg.message_id
                status_sent = True
            except Exception:
                logger.exception(
                    "Failed to send status message for chat %s", chat_id
                )

        pr_msg_id = session.current_message_ids.get("pr")
        if pr_urls:
            try:
                session.current_message_ids["pr"] = (
                    await self._send_pr_link_message(chat_id, pr_urls, pr_msg_id)
                    or pr_msg_id
                )
            except Exception:
                logger.exception("Failed to send PR message for chat %s", chat_id)
        elif pr_msg_id:
            try:
                await self.bot.delete_message(chat_id=chat_id, message_id=pr_msg_id)
            except Exception:
                pass
            session.current_message_ids.pop("pr", None)

        await self._send_detail_messages(session, chat_id, details)

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
