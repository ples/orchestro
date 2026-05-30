"""Tests for repo_verification."""

import json
from pathlib import Path
from unittest.mock import patch

from agent_graph.repo_verification import _detect_commands, run_checks


def test_detect_node_test_command(tmp_path: Path):
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"scripts": {"test": "jest", "lint": "eslint ."}}))
    cmds = _detect_commands(str(tmp_path))
    labels = [label for _, label in cmds]
    assert "npm test" in labels


@patch("agent_graph.repo_verification.verifier_run_tests", return_value=False)
def test_run_checks_skipped_when_disabled(_mock):
    result = run_checks("/tmp/repo")
    assert result.passed
    assert "disabled" in result.details


def test_run_checks_reports_missing_node_deps_when_bootstrap_disabled(tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}))
    (tmp_path / "package-lock.json").write_text("{}")
    result = run_checks(str(tmp_path))
    assert not result.passed
    assert "missing JS dependencies" in result.details


@patch("agent_graph.repo_verification.verifier_bootstrap_deps", return_value=True)
@patch("agent_graph.repo_verification._run_command")
def test_run_checks_bootstraps_node_deps_when_enabled(_mock_run, _mock_bootstrap, tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest"}}))
    (tmp_path / "package-lock.json").write_text("{}")
    _mock_run.side_effect = [(True, "installed"), (True, "tests")]
    result = run_checks(str(tmp_path))
    assert result.passed
    assert _mock_run.call_count == 2
    assert _mock_run.call_args_list[0].args[1] == ["npm", "ci"]
