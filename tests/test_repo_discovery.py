"""Tests for repo_discovery."""

import os
from pathlib import Path

from agent_graph.repo_discovery import run_discovery


def test_run_discovery_finds_file(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    target = src / "GroupDetails.tsx"
    target.write_text("export const memberCount = 0;\n")
    result = run_discovery(str(tmp_path), ["GroupDetails.tsx"])
    assert any("GroupDetails" in f for f in result.matched_files)
