"""Tests for batch planner workspace cloning."""

from unittest.mock import patch

import pytest

from agent_graph.exceptions import ExecutorError
from agent_graph.repo_clone import clone_planner_workspace


@patch("agent_graph.repo_clone._finalize_clone", return_value="abc123")
@patch("agent_graph.repo_clone._clone_into_path")
@patch(
    "agent_graph.repo_clone.subprocess.check_output",
    return_value="/tmp/planner_workspace_xyz\n",
)
def test_clone_planner_workspace_multiple(mock_mktemp, mock_clone_into, mock_finalize):
    urls = [
        "https://bitbucket.org/team/a.git",
        "https://bitbucket.org/team/b.git",
    ]
    parent, results = clone_planner_workspace(urls)

    assert parent == "/tmp/planner_workspace_xyz"
    assert len(results) == 2
    assert results[urls[0]][0].endswith("/a")
    assert results[urls[1]][0].endswith("/b")
    assert mock_clone_into.call_count == 2


@patch(
    "agent_graph.repo_clone.subprocess.check_output",
    return_value="/tmp/planner_workspace_xyz\n",
)
@patch("agent_graph.repo_clone._clone_into_path", side_effect=ExecutorError("fail"))
def test_clone_planner_workspace_propagates_error(mock_clone_into, mock_mktemp):
    with pytest.raises(ExecutorError, match="fail"):
        clone_planner_workspace(["https://bitbucket.org/team/a.git"])
