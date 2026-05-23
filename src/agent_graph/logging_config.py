"""Logging setup: quiet third-party noise, summaries on stdout, details at DEBUG."""

from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False

_LEVEL_NAMES = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}

_QUIET_LOGGERS = (
    "openhands",
    "openhands.sdk",
    "openhands.workspace",
    "openhands.tools",
    "litellm",
    "LiteLLM",
    "openai",
    "httpcore",
    "httpx",
    "libtmux",
    "uvicorn",
    "uvicorn.access",
    "uvicorn.error",
)


def _parse_level(name: str, default: int) -> int:
    return _LEVEL_NAMES.get(name.upper(), default)


def is_verbose() -> bool:
    """True when LOG_LEVEL=DEBUG (or legacy DEBUG=1)."""
    if os.getenv("DEBUG", "").lower() in ("1", "true", "yes"):
        return True
    return os.getenv("LOG_LEVEL", "WARNING").upper() == "DEBUG"


def configure_logging() -> None:
    """Apply logging once. Call before importing openhands SDK."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    if os.getenv("DEBUG", "").lower() in ("1", "true", "yes"):
        os.environ["LOG_LEVEL"] = "DEBUG"
    elif "LOG_LEVEL" not in os.environ:
        os.environ["LOG_LEVEL"] = "WARNING"
    os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")

    app_level = _parse_level(os.getenv("LOG_LEVEL", "WARNING"), logging.WARNING)

    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)

    app_logger = logging.getLogger("agent_graph")
    app_logger.setLevel(app_level)
    app_logger.propagate = True

    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def step(message: str) -> None:
    """User-facing workflow summary (always printed)."""
    print(message, flush=True)


def debug(message: str) -> None:
    """Detailed diagnostics (LOG_LEVEL=DEBUG only)."""
    logging.getLogger("agent_graph").debug(message)


def verbose_print(message: str) -> None:
    """Optional detail lines gated by LOG_LEVEL=DEBUG."""
    if is_verbose():
        print(message, flush=True)
