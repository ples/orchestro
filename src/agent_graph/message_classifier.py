"""LLM-based intent classification for Telegram free-text messages."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal, TypedDict

from agent_graph.llm_client import chat_completion
from agent_graph.main import classify_input
from agent_graph.state import TaskState, can_run_follow_up

logger = logging.getLogger(__name__)

IntentType = Literal[
    "start_task",
    "query_run",
    "adjust_run",
    "list_runs",
    "help",
    "ambiguous",
]

ACTIVE_WORKFLOW_NODES = frozenset(
    {"planning", "executing", "verifying", "pr_creating"}
)


class MessageIntent(TypedDict):
    intent: IntentType
    confidence: float
    task_text: str | None
    question: str | None
    adjustment: str | None
    run_id: str | None
    run_hint: str | None
    reason: str


class RunResolution(TypedDict):
    status: Literal["resolved", "ambiguous", "not_found"]
    run_id: str | None
    candidates: list[dict[str, Any]]


_CLASSIFIER_SYSTEM = """You classify Telegram messages for an engineering workflow bot.

Return ONLY a single JSON object. No markdown fences, no explanation, no thinking steps.
Plain-text feature requests without a ticket URL are valid start_task messages.

Required JSON fields:
- intent: one of start_task, query_run, adjust_run, list_runs, help, ambiguous
- confidence: float 0.0-1.0
- task_text: issue/ticket text for start_task, else null
- question: user question for query_run, else null
- adjustment: change instructions for adjust_run, else null
- run_id: exact run_id UUID if clearly referenced, else null
- run_hint: keywords to match a run ("JWT task", "last run", issue snippet), else null
- reason: one short sentence explaining the classification

Rules:
- start_task: user wants NEW implementation work (ticket, URL, feature request)
- query_run: questions about plan, PR, status, what was done on an existing run
- adjust_run: change/fix/update an existing completed run (not starting fresh work)
- list_runs: show history / past runs
- help: how to use the bot
- ambiguous: multiple plausible runs or intents; cannot decide

When session has an active workflow (not idle/completed/error/cancelled), query_run
about current work is OK. adjust_run on a different run while busy should be ambiguous.

For adjust_run the target must be a completed run that still allows follow-up."""


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def build_session_context(state: TaskState) -> str:
    node = state.get("workflow_node", "idle")
    run_id = state.get("run_id") or ""
    issue = _truncate(state.get("issue") or "", 200)
    can_adjust = can_run_follow_up(state)
    return (
        f"workflow_node={node}\n"
        f"run_id={run_id}\n"
        f"issue={issue!r}\n"
        f"can_run_follow_up={can_adjust}"
    )


def build_runs_context(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return "(no past runs)"
    lines: list[str] = []
    for i, run in enumerate(runs[:10], start=1):
        lines.append(
            f"{i}. run_id={run.get('run_id', '')} "
            f"node={run.get('workflow_node', '')} "
            f"created={run.get('created_at', '')} "
            f"issue={_truncate(run.get('issue', ''), 80)!r}"
        )
    return "\n".join(lines)


def _default_intent(
    intent: IntentType,
    *,
    confidence: float = 1.0,
    reason: str = "",
    **fields: str | None,
) -> MessageIntent:
    return MessageIntent(
        intent=intent,
        confidence=confidence,
        task_text=fields.get("task_text"),
        question=fields.get("question"),
        adjustment=fields.get("adjustment"),
        run_id=fields.get("run_id"),
        run_hint=fields.get("run_hint"),
        reason=reason,
    )


def _extract_json_blob(raw: str) -> str | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fenced:
        return fenced.group(1)

    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    for index, char in enumerate(raw[start:], start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw[start : index + 1]
    return None


def _parse_classifier_response(raw: str) -> MessageIntent | None:
    blob = _extract_json_blob(raw)
    if not blob:
        return None
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return None

    intent = data.get("intent")
    valid = {
        "start_task",
        "query_run",
        "adjust_run",
        "list_runs",
        "help",
        "ambiguous",
    }
    if intent not in valid:
        return None

    confidence = float(data.get("confidence", 0.5))
    return MessageIntent(
        intent=intent,
        confidence=max(0.0, min(1.0, confidence)),
        task_text=data.get("task_text") or None,
        question=data.get("question") or None,
        adjustment=data.get("adjustment") or None,
        run_id=data.get("run_id") or None,
        run_hint=data.get("run_hint") or None,
        reason=str(data.get("reason") or ""),
    )


def _url_fast_path(text: str) -> MessageIntent | None:
    classification = classify_input(text)
    if classification["type"] == "plain_text":
        return None
    return _default_intent(
        "start_task",
        confidence=0.95,
        task_text=text,
        reason="Message contains a ticket URL",
    )


def _looks_like_implementation_request(text: str) -> bool:
    lower = text.lower().strip()
    if len(lower) < 20:
        return False
    if "?" in lower:
        return False
    if any(
        lower.startswith(prefix)
        for prefix in ("what ", "how ", "when ", "where ", "why ", "show ", "list ")
    ):
        return False
    if any(
        marker in lower
        for marker in (
            "what was",
            "what is the plan",
            "run status",
            "pull request url",
            "/history",
        )
    ):
        return False
    if any(
        marker in lower
        for marker in (
            "implement",
            "add ",
            "create ",
            "fix ",
            "update ",
            "build ",
            "deploy",
            "trigger",
            "find ",
            "repo",
            "jenkins",
            "pipeline",
            "want to",
            "need to",
            "should ",
        )
    ):
        return True
    return len(lower) >= 80


def fallback_intent(text: str, state: TaskState) -> MessageIntent:
    fast = _url_fast_path(text)
    if fast:
        return fast

    node = state.get("workflow_node", "idle")
    if node == "completed" and can_run_follow_up(state):
        lower = text.lower()
        if any(
            w in lower
            for w in ("change", "update", "fix", "adjust", "instead", "also add")
        ):
            return _default_intent(
                "adjust_run",
                confidence=0.5,
                adjustment=text,
                reason="Fallback: completed session with adjustment-like wording",
            )

    if node in ("idle", "completed", "error", "cancelled", "") and _looks_like_implementation_request(
        text
    ):
        return _default_intent(
            "start_task",
            confidence=0.75,
            task_text=text,
            reason="Fallback: plain-text implementation request",
        )

    return _default_intent(
        "ambiguous",
        confidence=0.3,
        reason="Could not classify message; LLM unavailable or low confidence",
    )


def classify_user_message(
    text: str,
    state: TaskState,
    recent_runs: list[dict[str, Any]],
    *,
    focused_run_id: str | None = None,
) -> MessageIntent:
    text = text.strip()
    if not text:
        return _default_intent("ambiguous", confidence=0.0, reason="Empty message")

    fast = _url_fast_path(text)
    if fast:
        return fast

    user_prompt = (
        f"User message:\n{text}\n\n"
        f"Current session:\n{build_session_context(state)}\n\n"
        f"Recent runs:\n{build_runs_context(recent_runs)}"
    )
    if focused_run_id:
        user_prompt += f"\n\nUser is focused on run_id={focused_run_id} (Q&A thread)."

    raw = chat_completion(
        _CLASSIFIER_SYSTEM,
        user_prompt,
        max_tokens=256,
        temperature=0.0,
        json_mode=True,
    )
    if not raw:
        return fallback_intent(text, state)

    parsed = _parse_classifier_response(raw)
    if not parsed:
        logger.warning("Failed to parse classifier JSON: %s", raw[:200])
        return fallback_intent(text, state)

    if parsed["confidence"] < 0.6 and parsed["intent"] not in ("help", "list_runs"):
        parsed["intent"] = "ambiguous"
        parsed["reason"] = (
            parsed.get("reason") or ""
        ) + " (low confidence — needs clarification)"

    logger.info(
        "Classified message intent=%s confidence=%.2f reason=%s",
        parsed["intent"],
        parsed["confidence"],
        parsed.get("reason"),
    )
    return parsed


def _normalize_hint(hint: str) -> str:
    return re.sub(r"\s+", " ", hint.lower().strip())


def _hint_matches_run(hint: str, run: dict[str, Any]) -> bool:
    norm_hint = _normalize_hint(hint)
    if not norm_hint:
        return False

    if norm_hint in ("last", "last run", "latest", "most recent", "recent"):
        return True

    issue = _normalize_hint(run.get("issue") or "")
    run_id = (run.get("run_id") or "").lower()
    if run_id and run_id.startswith(norm_hint.replace("-", "")):
        return True

    hint_words = [w for w in re.split(r"[\s,.]+", norm_hint) if len(w) > 2]
    if not hint_words:
        return norm_hint in issue

    matches = sum(1 for w in hint_words if w in issue)
    return matches >= max(1, len(hint_words) // 2)


def resolve_run_reference(
    *,
    run_id: str | None,
    run_hint: str | None,
    runs: list[dict[str, Any]],
    confidence: float = 1.0,
) -> RunResolution:
    if run_id:
        for run in runs:
            if run.get("run_id") == run_id:
                return RunResolution(
                    status="resolved",
                    run_id=run_id,
                    candidates=[],
                )
        return RunResolution(status="not_found", run_id=None, candidates=[])

    if not runs:
        return RunResolution(status="not_found", run_id=None, candidates=[])

    hint = (run_hint or "").strip()
    if hint and _normalize_hint(hint) in (
        "last",
        "last run",
        "latest",
        "most recent",
        "recent",
    ):
        return RunResolution(
            status="resolved",
            run_id=runs[0]["run_id"],
            candidates=[],
        )

    if hint:
        matches = [r for r in runs if _hint_matches_run(hint, r)]
        if len(matches) == 1:
            return RunResolution(
                status="resolved",
                run_id=matches[0]["run_id"],
                candidates=[],
            )
        if len(matches) > 1:
            return RunResolution(
                status="ambiguous",
                run_id=None,
                candidates=matches,
            )

    if confidence >= 0.85 and len(runs) == 1:
        return RunResolution(
            status="resolved",
            run_id=runs[0]["run_id"],
            candidates=[],
        )

    return RunResolution(status="not_found", run_id=None, candidates=[])
