"""Tests for git_utils."""

import subprocess
from pathlib import Path

from agent_graph.git_utils import (
    capture_diff_since,
    commit_count_since,
    has_changes_since,
    rev_parse,
)


def _init_repo(path: Path) -> str:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    (path / "README.md").write_text("initial\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    return rev_parse(str(path))


def test_detects_uncommitted_changes(tmp_path: Path):
    baseline = _init_repo(tmp_path)
    (tmp_path / "new.txt").write_text("hello\n")
    assert has_changes_since(str(tmp_path), baseline) is True


def test_detects_committed_changes_since_baseline(tmp_path: Path):
    baseline = _init_repo(tmp_path)
    (tmp_path / "docker-compose.yml").write_text("services:\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "add compose"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    assert has_changes_since(str(tmp_path), baseline) is True
    assert commit_count_since(str(tmp_path), baseline) == 1
    diff = capture_diff_since(str(tmp_path), baseline)
    assert "docker-compose.yml" in diff


def test_no_changes_after_baseline_only(tmp_path: Path):
    baseline = _init_repo(tmp_path)
    assert has_changes_since(str(tmp_path), baseline) is False
