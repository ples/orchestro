"""Tests for constrained_executor."""

from unittest.mock import patch

from agent_graph.constrained_executor import _extract_patch, run_constrained
from agent_graph.repo_discovery import DiscoveryResult


def test_extract_patch_from_fence():
    text = "```diff\n--- a/foo.ts\n+++ b/foo.ts\n@@\n```"
    assert "--- a/foo.ts" in _extract_patch(text)


@patch("agent_graph.constrained_executor.OpenHandsClient")
@patch("agent_graph.constrained_executor.apply_unified_patch", return_value=(True, ""))
@patch("agent_graph.constrained_executor.has_changes_since", return_value=True)
@patch("agent_graph.constrained_executor.changed_files_since", return_value=["src/foo.ts"])
def test_run_constrained_success(
    mock_changed, _mock_has, _mock_apply, mock_client_cls, tmp_path
):
    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.ts").write_text("x\n")
    mock_client_cls.return_value._plan_with_local_llm.return_value = (
        "```diff\n--- a/src/foo.ts\n+++ b/src/foo.ts\n@@ -1 +1 @@\n-x\n+y\n```"
    )
    discovery = DiscoveryResult(matched_files=["src/foo.ts"])
    result = run_constrained(
        issue="Remove x",
        scoped_plan="Remove x from foo.ts",
        repo_path=str(tmp_path),
        baseline_sha="abc",
        discovery=discovery,
    )
    assert result.success
