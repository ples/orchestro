# Agent Graph

Minimal AI Engineering Agent MVP built with LangGraph. Automates code planning, execution,
verification, and automated GitHub pull request creation.

## Table of Contents

- [Quick Start](#quick-start)
  - [Step-by-Step](#step-by-step)
  - [Programmatic API](#programmatic-api)
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

#### 3. Run the workflow

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

#### 4. Install dev dependencies and run tests

```bash
pip install -e ".[dev]"
python -m pytest tests/ -v
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

## Graph Topology

The workflow is a directed state machine that ends after PR creation (or skip when there are no changes):

```mermaid
flowchart LR
    P["<b>planner</b><br/>Generate plan"] --> E["<b>executor</b><br/>Execute"]
    E --> V["<b>verifier</b><br/>Verify"]
    V --> PR["<b>pr_creator</b><br/>Open PR"]
    PR --> DONE["<b>end</b>"]
```

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
- `plan` — structured plan produced by the planner, ingested by the executor.
- `implementation_result` — summary of code changes from the executor.
- `verification_result` — test/lint/static-analysis output from the verifier.
- `work_repo_path` — host path to the temp clone with OpenHands edits (used by PR creator).
- `pr_url` — URL of the created pull request (empty if skipped or failed).
- `pr_error` — error message when PR creation failed.

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

## Tech Stack

| Layer             | Technology   | Purpose               |
|-------------------|------------- |-----------------------|
| Workflow engine   | LangGraph    | State-machine runner  |
| Schema validation | Pydantic     | Runtime type checks   |
| State definition  | `TypedDict`  | Lightweight interface |
| Linting           | Ruff         | Fast Python linter    |
| Static analysis   | mypy         | Type checking         |
| Testing           | pytest       | Unit & integration    |

## Requirements

- Python >= 3.11
- See `[project]` dependencies in [`pyproject.toml`](pyproject.toml) for the full list.

To install dev tooling in one step:

```bash
pip install -e ".[dev]"
```
