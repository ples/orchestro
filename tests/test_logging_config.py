"""Tests for logging configuration."""

import os

from agent_graph.logging_config import configure_logging, is_verbose


def test_is_verbose_default(monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("DEBUG", raising=False)
    configure_logging()
    assert is_verbose() is False


def test_is_verbose_with_debug_level(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    configure_logging()
    assert is_verbose() is True
