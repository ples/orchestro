#!/usr/bin/env bash
set -euo pipefail

# Usage: run-github.sh [owner/repo] [issue_number | issue_text]
# Examples:
#   ./scripts/run-github.sh ples/singer-tutor 42
#   ./scripts/run-github.sh ples/singer-tutor "Add JWT authentication to FastAPI backend"

REPO="${1:-}"
ISSUE="${2:-}"

if [[ -z "$REPO" ]]; then
    echo "Usage: $0 <owner/repo> [issue_number | issue_text]"
    echo ""
    echo "Examples:"
    echo "  $0 ples/singer-tutor 42"
    echo "  $0 ples/singer-tutor \"Add JWT authentication to FastAPI backend\""
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

if [[ -n "$ISSUE" ]]; then
    if [[ "$ISSUE" =~ ^#[0-9]+$ || "$ISSUE" =~ ^[0-9]+$ ]]; then
        python -m agent_graph.main --issue "$ISSUE" --github --github-repo "$REPO"
    else
        python -m agent_graph.main --issue "$ISSUE" --github --github-repo "$REPO"
    fi
else
    python -m agent_graph.main --github --github-repo "$REPO"
fi
