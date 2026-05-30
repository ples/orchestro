"""Shared OpenAI-compatible chat completion helper."""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)


def resolve_model(
    llm_base_url: str,
    llm_model: str,
    headers: dict[str, str],
) -> str:
    model = llm_model
    try:
        resp_models = requests.get(
            llm_base_url.rstrip("/") + "/models",
            headers=headers,
            timeout=5,
        )
        if resp_models.ok:
            ids = [m["id"] for m in resp_models.json().get("data", [])]
            bare = llm_model.split("/", 1)[-1]
            if llm_model not in ids and bare not in ids:
                model = ids[0] if ids else llm_model
            elif bare in ids:
                model = bare
    except Exception:
        pass
    return model


def chat_completion(
    system: str,
    user: str,
    *,
    max_tokens: int = 1024,
    temperature: float = 0.2,
    timeout: int = 120,
    json_mode: bool = False,
) -> str | None:
    """Call the configured LLM. Returns content or None on failure."""
    llm_base_url = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8555/v1")
    llm_api_key = os.getenv("LLM_API_KEY", "")
    llm_model = os.getenv("LLM_MODEL", "")

    url = llm_base_url.rstrip("/") + "/chat/completions"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if llm_api_key:
        headers["Authorization"] = f"Bearer {llm_api_key}"

    model = resolve_model(llm_base_url, llm_model, headers)
    payload: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    def _post(body: dict) -> str | None:
        resp = requests.post(url, headers=headers, json=body, timeout=timeout)
        resp.raise_for_status()
        return (
            resp.json()
            .get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )

    try:
        return _post(payload)
    except Exception as exc:
        if json_mode and "response_format" in payload:
            logger.debug("LLM json_mode failed, retrying without: %s", exc)
            plain = dict(payload)
            plain.pop("response_format", None)
            try:
                return _post(plain)
            except Exception as retry_exc:
                logger.warning("LLM chat completion failed: %s", retry_exc)
                return None
        logger.warning("LLM chat completion failed: %s", exc)
        return None
