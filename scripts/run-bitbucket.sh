#!/usr/bin/env bash
set -euo pipefail

# Usage: run-bitbucket.sh <owner/repo> [issue_id | issue_text]
# Examples:
#   ./scripts/run-bitbucket.sh myteam/myrepo 42
#   ./scripts/run-bitbucket.sh myteam/myrepo "Fix login bug"

REPO="${1:-}"
ISSUE="${2:-}"

if [[ -z "$REPO" ]]; then
    echo "Usage: $0 <owner/repo> [issue_id | issue_text]"
    echo ""
    echo "Examples:"
    echo "  $0 myteam/myrepo 42"
    echo "  $0 myteam/myrepo \"Fix login bug\""
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

if [[ -n "$ISSUE" ]]; then
    # Prefix issue_id with # so the fetcher recognizes it as an ID
    if [[ "$ISSUE" =~ ^#[0-9]+$ || "$ISSUE" =~ ^[0-9]+$ ]]; then
        python -m agent_graph.main --issue "$ISSUE" --bitbucket --bitbucket-repo "$REPO"
    else
        python -m agent_graph.main --issue "$ISSUE" --bitbucket --bitbucket-repo "$REPO"
    fi
else
    python -m agent_graph.main --bitbucket --bitbucket-repo "$REPO"
fi
