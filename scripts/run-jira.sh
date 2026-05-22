#!/usr/bin/env bash
set -euo pipefail

# Usage: run-jira.sh <project_key> [issue_key | issue_text] [developer_prompt]
# Examples:
#   ./scripts/run-jira.sh PROJ-123
#   ./scripts/run-jira.sh PROJ "Add OAuth2 login flow"
#   ./scripts/run-jira.sh PROJ MINSKY-123 "Also fix backend email verified flag"

PROJECT_KEY="${1:-}"
ISSUE="${2:-}"
PROMPT="${3:-}"

if [[ -z "$PROJECT_KEY" ]]; then
    echo "Usage: $0 <project_key> [issue_key | issue_text] [developer_prompt]"
    echo ""
    echo "Examples:"
    echo "  $0 PROJ-123"
    echo "  $0 PROJ \"Add OAuth2 login flow\""
    echo "  $0 PROJ MINSKY-123 \"Also fix backend email verified flag\""
    exit 1
fi

PROMPT_ARGS=()
if [[ -n "$PROMPT" ]]; then
    PROMPT_ARGS=(--prompt "$PROMPT")
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

if [[ -n "$ISSUE" ]]; then
    if [[ "$ISSUE" =~ ^[A-Za-z]+-[0-9]+$ ]]; then
        # Jira issue key - fetch directly
        python -m agent_graph.main --issue "$ISSUE" --jira --jira-project "$PROJECT_KEY" --jql "key = ${ISSUE}" "${PROMPT_ARGS[@]}"
    else
        # Free-form text - search for issues in the project
        python -m agent_graph.main --issue "$ISSUE" --jira --jira-project "$PROJECT_KEY" "${PROMPT_ARGS[@]}"
    fi
else
    python -m agent_graph.main --jira --jira-project "$PROJECT_KEY" "${PROMPT_ARGS[@]}"
fi
