# Agent Graph

Minimal AI Engineering Agent MVP built with LangGraph. Automates code planning, execution,
verification, and automated GitHub pull request creation.

## Table of Contents

- [Quick Start](#quick-start)
  - [Step-by-Step](#step-by-step)
  - [Programmatic API](#programmatic-api)
- [Telegram Bot](#telegram-bot)
- [Graph Topology](#graph-topology)
- [Data Model](#data-model)
- [Agent Architecture](#agent-architecture)
- [Exception Hierarchy](#exception-hierarchy)
- [Tech Stack](#tech-stack)
- [Requirements](#requirements)

## Quick Start

### Step-by-Step

#### 1. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate   # on Windows: .venv\Scripts\activate
```

#### 2. Install the package (in editable mode)

```bash
pip install -e .
```

This builds `agent_graph` from source, so any code change is reflected immediately.

#### 3. Start PostgreSQL (required for run history + LangGraph checkpoints)

```bash
docker compose up -d postgres
```

Default local credentials (see `.env.example`):

- `POSTGRES_HOST=127.0.0.1`
- `POSTGRES_PORT=5432`
- `POSTGRES_DB=agent_graph`
- `POSTGRES_USER=agent_graph`
- `POSTGRES_PASSWORD=agent_graph`

Recommended DSN configuration:

```bash
export RUNS_DB_URL="postgresql://agent_graph:agent_graph@127.0.0.1:5432/agent_graph"
export LANGGRAPH_CHECKPOINT_DB_URL="$RUNS_DB_URL"
```

If `RUNS_DB_URL` is not set, the app builds a DSN from `POSTGRES_*` values.

#### 4. Run the workflow

```bash
python -m agent_graph.main
```

Expected terminal output:

```
[Planner]
Analyzing issue: Add JWT authentication to FastAPI backend

[Executor / OpenHands]
Executing implementation plan...

1. Inspect repository
2. Add implementation for: Add JWT authentication to FastAPI backend
3. Run tests
4. Create patch

[Verifier]
Running verification pipeline...

[PR Creator]
  Pull request created: https://github.com/owner/repo/pull/42
```

Requires `GITHUB_TOKEN` with permission to push branches and open pull requests.
If there are no file changes, the workflow completes without opening a PR.

Custom task:

```bash
python -m agent_graph.main --issue "Add OAuth2 login flow"
```

Jira ticket with extra developer context (scope beyond the ticket body):

```bash
python -m agent_graph.main \
  --issue "https://company.atlassian.net/browse/MINSKY-123" \
  --repo "https://bitbucket.org/team/admin-ui.git,https://bitbucket.org/team/api.git" \
  --prompt "Fix email verified flag in both frontend and backend; backend is source of truth."
```

Deploy environment git tag (after branch push, before/at PR creation):

```bash
python -m agent_graph.main \
  --issue "https://company.atlassian.net/browse/MINSKY-123" \
  --repo "https://bitbucket.org/team/api.git" \
  --deploy-env dev
```

The tag format is `env.{env}.branch.{branch}` (e.g. `env.dev.branch.hotfix/my-fix`). The tag is created at the commit HEAD and pushed in the same `git push` as the branch so CI/CD sees it when the branch webhook fires. You can also set `DEPLOY_ENV` in `.env` or infer the environment from `--prompt` / issue text (e.g. `deploy to stage`).

#### 5. Install dev dependencies and run tests

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
```

### PostgreSQL Persistence Notes

- Workflow run history is stored in PostgreSQL table `workflow_runs`.
- LangGraph checkpointer state is stored in PostgreSQL (via `langgraph-checkpoint-postgres`).
- Existing SQLite data is not migrated; this setup starts fresh on PostgreSQL.
- If startup fails with connection errors, check DB health:

```bash
docker compose ps postgres
docker compose logs postgres
```

### Programmatic API

```python
from agent_graph import build_graph
from agent_graph.state import TaskState

# Build the compiled LangGraph StateGraph
graph = build_graph()

# Define initial state
state: TaskState = {
    "issue": "Add logout endpoint",
    "input_prompt": "",
    "plan": "",
    "implementation_result": "",
    "verification_result": "",
    "target_repo_path": "https://github.com/owner/repo.git",
    "work_repo_path": "",
    "diff_patch": "",
    "github_issue_url": "",
    "pr_url": "",
    "pr_error": "",
}

# Execute the workflow
result = graph.invoke(state)
```

Override any agent node with a custom function (useful for CI or testing):

```python
from agent_graph import build_graph


def mock_pr_creator(state: dict) -> dict:
    return {"pr_url": "https://github.com/owner/repo/pull/1", "pr_error": ""}


graph = build_graph(pr_creator_fn=mock_pr_creator)
result = graph.invoke(state)
```

## Telegram Bot

Run the agent workflow from Telegram instead of the CLI. The bot uses long polling and runs the same LangGraph pipeline (plan → execute → verify → PR).

### Prerequisites

Same as [Quick Start](#quick-start): Python 3.12+, virtualenv, `pip install -e .`, a running LLM (`LLM_*` in `.env`), Docker for OpenHands, and tokens for PR creation (`GITHUB_TOKEN` or Bitbucket/Jira settings as needed).

### 1. Create a bot token

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, follow the prompts, and copy the token.

### 2. Configure environment

Copy `.env.example` to `.env` if you have not already, then set at minimum:


| Variable                                   | Purpose                                                                  |
| ------------------------------------------ | ------------------------------------------------------------------------ |
| `TELEGRAM_BOT_TOKEN`                       | Token from BotFather                                                     |
| `GITHUB_TOKEN`                             | Push branches and open pull requests                                     |
| `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL` | LLM used by planner/executor                                             |
| `TARGET_REPO_PATH`                         | Default repo (optional when the issue text or Jira ticket implies repos) |


For Bitbucket/Jira workflows, also configure `BITBUCKET_TOKEN`, `JIRA_*`, and related vars from `[.env.example](.env.example)`.

### 3. Start the bot server

Activate your virtualenv, then run:

```bash
agent-graph-telegram
```

The process stays in the foreground and polls Telegram for messages. Stop it with `Ctrl+C`.

You should see `Telegram bot started in polling mode` in the logs. If `TELEGRAM_BOT_TOKEN` is missing, the command exits with setup instructions.

### 4. Use in Telegram Messenger

1. Open your bot in Telegram (use the link from BotFather or search by username).
2. Send `/start` — the bot resets your session and is ready for a new task.
3. Send `/help` for commands, workflow tips, and button reference.
4. Send a task as a plain-text message, for example:
  - `Add JWT authentication to FastAPI backend`
  - `https://company.atlassian.net/browse/MINSKY-123`
5. The bot updates one status message as the workflow progresses: **Planning** → **Executing** → **Verifying** → **Creating pull request**.
6. When finished, you get the PR link, a skip reason, or an error message.

**Commands**

- `/start` — Reset session and start a new task
- `/help` — Show help (commands, workflow, buttons)
- `/history` — List recent runs for this chat (alias: `/runs`)

**History & Q&A:** Use `/history` to browse past runs. Tap a run for details, **View Plan**, or **Ask Question** to ask about that run. Send questions as messages; tap **Exit Q&A** when done.

**Inline buttons**


| Button        | When                   | Action                            |
| ------------- | ---------------------- | --------------------------------- |
| **Start**     | Idle                   | Prompts you to send a task        |
| **Run Again** | During or after a run  | Clears the session for a new task |
| **Adjust**    | After a successful run | Asks for adjustment instructions  |
| **Retry**     | After an error         | Re-runs the workflow              |


**Follow-up adjustments:** After completion, send another message with changes (e.g. `Use a blue button; add a unit test`) or tap **Adjust**. One adjustment pass is allowed per task; it reuses the existing branch and updates the PR. See [Follow-up adjustment](#follow-up-adjustment-one-iteration).

**Natural language:** You can write freely — the bot classifies your intent:

| Example | Intent |
| ------- | ------ |
| `Implement OAuth for admin-ui` | Start new work |
| `What's the PR link for the JWT task?` | Ask about a past run |
| `Make the login button blue on the last task` | Adjust a completed run |
| `show my runs` | List history |

If several runs match, the bot asks you to pick one. Sending a new task while a workflow is running cancels the current run and starts fresh.

## Graph Topology

The workflow is a directed state machine that ends after PR creation (or skip when there are no changes).
For Jira/Bitbucket issues, `repo_resolver` runs first to detect target repositories from the issue text.

```mermaid
flowchart LR
    RR["repo_resolver"] --> P["planner"]
    PA["plan_adjuster"] --> E["executor_loop"]
    P --> E
    E --> V["verifier"]
    V --> PR["pr_aggregator"]
    PR --> DONE["end"]
```



Entry routing: `follow_up` mode skips planner/repo_resolver and starts at `plan_adjuster`.

**Planner phase:** clones each detected repo, runs a static dependency scan, then OpenHands in Docker (read-only analysis) to produce per-repo `repo_summary` and an aggregated `plan`.

**Executor phase:** hybrid pipeline per repository:

- Skips repos with `requires_changes=false` (no OpenHands run).
- **Constrained path** for simple localized fixes: host-side discovery, local LLM patch, then git apply.
- **OpenHands path** for standard/complex work (repo-scoped plan, discovery instructions, tiered `max_iterations`).
- Required repos with no diff after one retry are marked failed (not silent success).

**Verifier phase:** state checks plus optional repo tests/lint (`npm test`, `pytest`, etc.) when `VERIFIER_RUN_TESTS=1`.

### Follow-up adjustment (one iteration)

After a completed run, you can request **one** adjustment pass that modifies existing work instead of starting over:

- Reuses `work_repo_path` and does not reset to baseline
- Skips full planner; `plan_adjuster` merges prior plan, diff, and your adjustment prompt
- Pushes to the same branch / updates the existing PR

**CLI:**

```bash
python -m agent_graph.main --issue "..." --state-out run.json
python -m agent_graph.main --state-in run.json --follow-up "Use blue button; add a unit test"
```

**Telegram:** when a workflow is completed, send a message with adjustment instructions or tap **Adjust**. See [Telegram Bot](#telegram-bot).

## Data Model

All agents communicate through a single `TaskState` `TypedDict`:

```mermaid
classDiagram
    class TaskState {
        +str issue
        +str plan
        +str implementation_result
        +str verification_result
        +str pr_url
        +str pr_error
        +str work_repo_path
    }
```



- `issue` — original task description.
- `plan` — aggregated implementation plan from the planner (includes per-repo OpenHands analysis).
- `target_repos` — list of `RepoRecord` entries (`target_repo_path`, `planner_clone_path`, `repo_summary`, `work_repo_path`, …).
- `implementation_result` — summary of code changes from the executor.
- `verification_result` — test/lint/static-analysis output from the verifier.
- `work_repo_path` — host path to the clone with OpenHands edits (used by PR creator).
- `pr_url` — URL of the created pull request (empty if skipped or failed).
- `pr_error` — error message when PR creation failed.

Optional env: `GIT_CLONE_TIMEOUT` (default 120s), `PLANNER_MAX_ITERATIONS` (default 80), `EXECUTOR_MAX_ITERATIONS` (default 120), `EXECUTOR_SIMPLE_MAX_ITERATIONS` (60), `EXECUTOR_COMPLEX_MAX_ITERATIONS` (500), `EXECUTOR_CONSTRAINED_ENABLED` (1), `VERIFIER_RUN_TESTS` (1), `VERIFIER_SKIP_LINT` (0), `VERIFIER_BOOTSTRAP_DEPS` (0; auto-install JS deps before checks), `PR_ALLOW_HARD_FORCE_PUSH` (1; allow hard-force fallback by default, set `0` to block).

Pydantic models keep structured payloads typed and validated:

```mermaid
classDiagram
    class ExecutionResult {
        +bool success
        +str summary
        +str work_repo_path
        +str diff_patch
    }
    class VerificationResult {
        +bool passed
        +str details
    }
```



## Agent Architecture

Every agent implements the `AgentProtocol` and extends `BaseAgent`, which provides automatic
logging and error handling:

```mermaid
classDiagram
    class AgentProtocol {
        <<abstract>>
        +run(state) dict
    }
    class BaseAgent {
        +name: str
        +run(state) dict
        <<abstract>>
        +_execute(state) dict
    }
    class PlannerAgent {
        +name = "planner"
        +_execute(state) dict
    }
    class ExecutorAgent {
        +name = "executor"
        +_execute(state) dict
    }
    class VerifierAgent {
        +name = "verifier"
        +_execute(state) dict
    }
    class PrCreatorAgent {
        +name = "pr_creator"
        +_execute(state) dict
    }
    AgentProtocol <|-- BaseAgent
    BaseAgent <|-- PlannerAgent
    BaseAgent <|-- ExecutorAgent
    BaseAgent <|-- VerifierAgent
    BaseAgent <|-- PrCreatorAgent
```



Factory functions live in `graph_builder.py`. The `build_graph()` function accepts optional
callback overrides so you can swap in mock or production agents without changing the graph itself.

## Exception Hierarchy

All agents raise from a common `AgentError` base so callers can wrap the full pipeline in a
single `try/except`:

```mermaid
classDiagram
    class AgentError {
        +str message
        +AgentErrorType | None error_type
    }
    class PlannerError
    class ExecutorError
    class VerifierError
    class PrCreationError
    class AgentErrorType {
        <<enumeration>>
        PLANNER
        EXECUTOR
        VERIFIER
    }
    AgentError <|-- PlannerError
    AgentError <|-- ExecutorError
    AgentError <|-- VerifierError
    AgentError <|-- PrCreationError
```



## MCP Integration

The agent graph can use [Model Context Protocol](https://modelcontextprotocol.io/) servers for documentation lookup, Jira ingest, and (optionally) OpenHands executor tools.

### Configuration

- Project config: `[mcp.config.json](mcp.config.json)` — enable servers and set `transport` (`stdio` or `http`)
- Secrets: `.env` only (e.g. `CONTEXT7_API_KEY`, `JIRA_CLOUD_ID`)
- Optional merge from Cursor: `MCP_USE_CURSOR_CONFIG=1` reads `~/.cursor/mcp.json` (project entries override)

### Host-side MCP (planner)

When the issue mentions libraries (FastAPI, LangGraph, React, etc.), the planner calls **Context7** via local stdio (`npx -y @upstash/context7-mcp`) and appends docs to `repo_context`.

Disable with `MCP_CONTEXT7_ENABLED=0`.

Requires `npx` on the host (same as Cursor).

### Jira via MCP (optional)

```bash
python -m agent_graph.main --jira --jira-mcp --jira-project PROJ
```

Or set `JIRA_USE_MCP=1` and enable the `atlassian` server in `mcp.config.json`. REST fetcher remains the default.

### Executor MCP (OpenHands)

Set `OPENHANDS_MCP_ENABLED=1` to pass MCP servers into the OpenHands `Agent` inside Docker. By default only **HTTP** servers are included (`OPENHANDS_MCP_HTTP_ONLY=1`) because stdio/`npx` may be unavailable in the agent-server image.

## Tech Stack


| Layer             | Technology  | Purpose               |
| ----------------- | ----------- | --------------------- |
| Workflow engine   | LangGraph   | State-machine runner  |
| Schema validation | Pydantic    | Runtime type checks   |
| State definition  | `TypedDict` | Lightweight interface |
| Linting           | Ruff        | Fast Python linter    |
| Static analysis   | mypy        | Type checking         |
| Testing           | pytest      | Unit & integration    |


## Requirements

- Python >= 3.11
- See `[project]` dependencies in `[pyproject.toml](pyproject.toml)` for the full list.

To install dev tooling in one step:

```bash
pip install -e ".[dev]"
```

