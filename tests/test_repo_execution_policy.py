"""Tests for repo execution policy helpers."""

from agent_graph.repo_execution_policy import (
    apply_execution_contract,
    build_repo_execution_plan,
    extract_expected_targets,
    infer_execution_profile,
    infer_requires_changes,
    max_iterations_for_profile,
)


def test_infer_requires_changes_from_repo_roles():
    overview = (
        "## Repo roles\n"
        "- **admin-ui**: only repository requiring code changes.\n"
        "- **identity-hub-api**: No action required.\n"
    )
    assert infer_requires_changes(
        "https://bitbucket.org/dmetrics/admin-ui.git",
        "",
        cross_repo_context=overview,
        all_repo_urls=[
            "https://bitbucket.org/dmetrics/admin-ui.git",
            "https://bitbucket.org/dmetrics/identity-hub-api.git",
        ],
    )
    assert not infer_requires_changes(
        "https://bitbucket.org/dmetrics/identity-hub-api.git",
        "",
        cross_repo_context=overview,
        all_repo_urls=[
            "https://bitbucket.org/dmetrics/admin-ui.git",
            "https://bitbucket.org/dmetrics/identity-hub-api.git",
        ],
    )


def test_extract_expected_targets_from_summary():
    summary = (
        "## Proposed changes\n"
        "- Remove counts from `src/components/GroupDetails.tsx`\n"
        "## Implementation steps\n"
        "1. Open GroupOverview component\n"
    )
    targets = extract_expected_targets(summary)
    assert "src/components/GroupDetails.tsx" in targets


def test_apply_execution_contract_sets_fields():
    repos = apply_execution_contract(
        [
            {
                "target_repo_path": "https://bitbucket.org/dmetrics/admin-ui.git",
                "repo_summary": (
                    "## Proposed changes\n"
                    "- Update `src/pages/GroupDetails.tsx`\n"
                ),
            },
            {
                "target_repo_path": "https://bitbucket.org/dmetrics/identity-hub-api.git",
                "repo_summary": "## Findings\nNo action required.",
            },
        ],
        cross_repo_context=(
            "## Repo roles\n"
            "- **admin-ui**: requiring code changes\n"
            "- **identity-hub-api**: No action required\n"
        ),
    )
    assert repos[0]["requires_changes"] is True
    assert "src/pages/GroupDetails.tsx" in repos[0]["expected_targets"]
    assert repos[1]["requires_changes"] is False


def test_infer_requires_changes_no_changes_needed_phrase():
    assert not infer_requires_changes(
        "https://bitbucket.org/dmetrics/account-ui.git",
        "## Findings\nNo code changes needed in this repo.",
        all_repo_urls=[
            "https://bitbucket.org/dmetrics/account-ui.git",
            "https://bitbucket.org/dmetrics/identity-hub-api.git",
        ],
    )


def test_apply_execution_contract_respects_prompt_focus():
    repos = apply_execution_contract(
        [
            {
                "target_repo_path": "https://bitbucket.org/dmetrics/identity-hub-api.git",
                "repo_summary": "## Proposed changes\n- Update `src/UserService.java`",
            },
            {
                "target_repo_path": "https://bitbucket.org/dmetrics/account-ui.git",
                "repo_summary": "## Implementation steps\n1. Explore files",
            },
        ],
        input_prompt="we should fix identity hub api endpoint only",
    )
    assert repos[0]["requires_changes"] is True
    assert repos[1]["requires_changes"] is False


def test_apply_execution_contract_strict_single_prompt_focus():
    repos = apply_execution_contract(
        [
            {
                "target_repo_path": "https://bitbucket.org/dmetrics/identity-hub-api.git",
                "repo_summary": "## Findings\nNo action required",
            },
            {
                "target_repo_path": "https://bitbucket.org/dmetrics/admin-ui.git",
                "repo_summary": "## Proposed changes\n- Update `src/a.tsx`",
            },
        ],
        input_prompt="focus only on identity-hub-api backend",
    )
    assert repos[0]["requires_changes"] is True
    assert repos[1]["requires_changes"] is False


def test_build_repo_execution_plan_scoped_to_repo():
    plan = build_repo_execution_plan(
        "=== PLAN ===\n## Implementation plan\nLong global text",
        {
            "target_repo_path": "https://bitbucket.org/dmetrics/admin-ui.git",
            "repo_summary": (
                "## Proposed changes\n"
                "- Remove counts from `src/GroupDetails.tsx`\n"
            ),
            "expected_targets": ["src/GroupDetails.tsx"],
        },
    )
    assert "admin-ui.git" in plan
    assert "Do not modify other repositories" in plan
    assert "GroupDetails.tsx" in plan


def test_execution_profile_simple():
    profile = infer_execution_profile(
        {
            "requires_changes": True,
            "repo_summary": "Remove the member count display",
            "expected_targets": ["src/a.tsx"],
        },
        required_repo_count=1,
    )
    assert profile == "simple"
    assert max_iterations_for_profile(profile) == 60
