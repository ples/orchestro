"""Serialize and deserialize TaskState for follow-up CLI runs."""

import json
from pathlib import Path
from typing import Any

from agent_graph.state import TaskState


def save_task_state(path: str | Path, state: TaskState) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(dict(state), f, indent=2, default=str)


def load_task_state(path: str | Path) -> TaskState:
    p = Path(path)
    with p.open(encoding="utf-8") as f:
        raw: dict[str, Any] = json.load(f)
    return _coerce_task_state(raw)


def _coerce_task_state(raw: dict[str, Any]) -> TaskState:
    state: TaskState = {}
    for key, value in raw.items():
        if key == "target_repos" and isinstance(value, list):
            state["target_repos"] = [dict(r) for r in value]
        elif key == "iteration":
            state["iteration"] = int(value) if value is not None else 0
        else:
            state[key] = value  # type: ignore[literal-required]
    return state
