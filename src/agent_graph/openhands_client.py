"""OpenHands client: spawns a per-task agent-server container via DockerWorkspace."""

import os
import platform

import requests

from agent_graph.logging_config import configure_logging, debug, step, verbose_print

configure_logging()

from openhands.sdk import LLM, Agent, Conversation, Tool
from openhands.sdk.conversation.exceptions import ConversationRunError
from openhands.sdk.utils.command import execute_command
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.terminal import TerminalTool
from openhands.workspace import DockerWorkspace

from agent_graph.exceptions import ExecutorError
from agent_graph.git_utils import (
    capture_diff_since,
    change_summary,
    diff_stat_since,
    has_changes_since,
)
from agent_graph.mcp.config import build_openhands_mcp_config
from agent_graph.models import ExecutionResult, PlanningResult
from agent_graph.repo_clone import clone_repository, reset_repo_to_baseline

DEFAULT_SERVER_IMAGE = "ghcr.io/openhands/agent-server:1.21.1-python"
LLM_ENV_KEYS = ("LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL")
DEFAULT_PLANNER_MAX_ITERATIONS = 20
DEFAULT_EXECUTOR_MAX_ITERATIONS = 500


def reuse_agent_server() -> bool:
    raw = os.getenv("OPENHANDS_REUSE_AGENT_SERVER", "1").strip().lower()
    return raw not in ("0", "false", "no")


class AgentServerSession:
    """One agent-server container; run multiple OpenHands conversations on it."""

    def __init__(
        self,
        client: "OpenHandsClient",
        volume_mounts: list[tuple[str, str]],
        *,
        skip_health_checks: bool = False,
    ):
        self._client = client
        self._volume_mounts = [
            (os.path.abspath(host), name) for host, name in volume_mounts
        ]
        self._skip_health_checks = skip_health_checks
        self._workspace: DockerWorkspace | None = None
        self._env_backup: dict[str, str | None] | None = None
        self._health_checked = False

    def start(self) -> None:
        if self._workspace is not None:
            return
        if not self._volume_mounts:
            raise RuntimeError("AgentServerSession requires at least one volume mount")

        volumes = [
            f"{host}:/workspace/{name}" for host, name in self._volume_mounts
        ]
        self._env_backup = self._client._apply_container_llm_env()
        self._workspace = DockerWorkspace(
            server_image=self._client.server_image,
            platform=self._client._detect_platform(),
            working_dir="/workspace",
            forward_env=list(LLM_ENV_KEYS),
            volumes=volumes,
            detach_logs=False,
        )
        step(
            f"  Agent server (shared): {self._workspace.host} "
            f"({len(self._volume_mounts)} repo mount(s))"
        )

        if self._skip_health_checks:
            return
        if not self._client._check_docker_available():
            raise RuntimeError("Docker is not available")
        if not self._client._check_llm_health():
            raise RuntimeError("LLM endpoint not reachable")
        if not self._client._check_llm_health_in_container(self._workspace):
            raise RuntimeError("LLM not reachable from agent-server container")
        self._health_checked = True

    def stop(self) -> None:
        if self._workspace is not None:
            self._workspace.cleanup()
            self._workspace = None
        if self._env_backup is not None:
            self._client._restore_env(self._env_backup)
            self._env_backup = None

    @property
    def workspace(self) -> DockerWorkspace:
        if self._workspace is None:
            msg = "Agent server session not started; call start() first"
            raise RuntimeError(msg)
        return self._workspace


class OpenHandsClient:
    """Delegates coding tasks to OpenHands by spawning an isolated agent-server container."""

    def __init__(
        self,
        llm_model: str | None = None,
        llm_api_key: str | None = None,
        llm_base_url: str | None = None,
        server_image: str | None = None,
    ):
        self.llm_model = llm_model or os.getenv(
            "LLM_MODEL", "openai/Qwen3.6-35B-A3B-UD-MLX-4bit"
        )
        self.llm_api_key = llm_api_key or os.getenv("LLM_API_KEY", "zaqw")
        self.llm_base_url = llm_base_url or os.getenv(
            "LLM_BASE_URL", "http://127.0.0.1:8555/v1"
        )
        self.server_image = server_image or os.getenv(
            "OPENHANDS_SERVER_IMAGE", DEFAULT_SERVER_IMAGE
        )

    @staticmethod
    def _planner_max_iterations() -> int:
        raw = os.getenv("PLANNER_MAX_ITERATIONS", "").strip()
        if raw.isdigit():
            return int(raw)
        return DEFAULT_PLANNER_MAX_ITERATIONS

    @staticmethod
    def _executor_max_iterations() -> int:
        raw = os.getenv("EXECUTOR_MAX_ITERATIONS", "").strip()
        if raw.isdigit():
            return int(raw)
        return DEFAULT_EXECUTOR_MAX_ITERATIONS

    def _prepare_repo(
        self,
        target_repo: str,
        *,
        existing_repo_path: str | None = None,
        baseline_sha: str | None = None,
        clone_prefix: str = "openhands_repo_",
        reset_to_baseline: bool = True,
    ) -> tuple[str, str]:
        if existing_repo_path and os.path.isdir(existing_repo_path):
            repo_path = existing_repo_path
            base = baseline_sha or ""
            if base and reset_to_baseline:
                reset_repo_to_baseline(repo_path, base)
            return repo_path, base

        if not target_repo:
            return "", ""

        try:
            return clone_repository(target_repo, prefix=clone_prefix)
        except ExecutorError:
            raise
        except OSError as exc:
            raise ExecutorError(str(exc)) from exc

    def run_cross_repo_planning(
        self,
        issue: str,
        combined_context: str,
        *,
        input_prompt: str = "",
    ) -> str:
        """Big-picture analysis across all cloned repositories (local LLM)."""
        if not combined_context.strip():
            return ""
        task = self._format_task_for_prompt(issue, input_prompt)
        prompt = (
            f"# Task\n\nIssue: {task}\n\n"
            f"# All target repositories (cloned)\n\n{combined_context}\n\n"
            f"# Instructions\n\n"
            f"1. Follow developer instructions when they narrow or expand scope beyond the ticket.\n"
            f"2. Explain how these repositories relate to the issue.\n"
            f"3. Identify which repo(s) likely need code changes and why.\n"
            f"4. Note shared APIs, types, or data flows between repos.\n"
            f"5. End with exactly these sections:\n\n"
            f"## Cross-repo findings\n\n"
            f"## Repo roles\n"
            f"(bullet per repository)\n\n"
            f"## Suggested per-repo focus\n"
            f"(what each repo analysis should prioritize)\n"
        )
        return self._plan_with_local_llm(prompt)

    def run_planning_task(
        self,
        *,
        target_repo: str,
        issue: str,
        static_context: str = "",
        existing_repo_path: str | None = None,
        baseline_sha: str | None = None,
        skip_health_checks: bool = False,
        big_picture: str = "",
        input_prompt: str = "",
        session: AgentServerSession | None = None,
    ) -> PlanningResult:
        try:
            repo_path, base = self._prepare_repo(
                target_repo,
                existing_repo_path=existing_repo_path,
                baseline_sha=baseline_sha,
                clone_prefix="planner_repo_",
            )
        except ExecutorError as exc:
            return PlanningResult(success=False, summary=str(exc))

        if not repo_path:
            return PlanningResult(
                success=False,
                summary="No repository path available for planning",
            )

        workspace_dir = f"/workspace/{os.path.basename(repo_path)}"
        prompt = self._build_planning_prompt(
            issue,
            static_context,
            workspace_dir,
            big_picture=big_picture,
            input_prompt=input_prompt,
        )

        if os.getenv("PLANNER_USE_OPENHANDS", "").lower() not in ("1", "true", "yes"):
            summary = self._plan_with_local_llm(prompt)
            if summary:
                reset_repo_to_baseline(repo_path, base)
                return PlanningResult(
                    success=True,
                    summary=summary,
                    planner_clone_path=repo_path,
                    repo_baseline_sha=base,
                )
            return PlanningResult(
                success=False,
                summary="Local LLM planning failed",
                planner_clone_path=repo_path,
                repo_baseline_sha=base,
            )

        result = self._execute_in_docker_workspace(
            repo_path,
            workspace_dir,
            prompt,
            base,
            mode="planning",
            skip_health_checks=skip_health_checks or session is not None,
            check_changes=False,
            session=session,
        )

        reset_repo_to_baseline(repo_path, base)

        if not result.success:
            return PlanningResult(
                success=False,
                summary=result.summary,
                planner_clone_path=repo_path,
                repo_baseline_sha=base,
            )

        return PlanningResult(
            success=True,
            summary=result.summary,
            planner_clone_path=repo_path,
            repo_baseline_sha=base,
        )

    def run_task(
        self,
        target_repo: str,
        issue: str,
        plan: str,
        existing_repo_path: str | None = None,
        baseline_sha: str | None = None,
        skip_health_checks: bool = False,
        input_prompt: str = "",
        session: AgentServerSession | None = None,
        *,
        follow_up: bool = False,
        follow_up_prompt: str = "",
        diff_stat: str = "",
    ) -> ExecutionResult:
        try:
            repo_path, base = self._prepare_repo(
                target_repo,
                existing_repo_path=existing_repo_path,
                baseline_sha=baseline_sha,
                reset_to_baseline=not follow_up,
            )
        except ExecutorError as exc:
            return ExecutionResult(success=False, summary=str(exc))

        workspace_dir = (
            f"/workspace/{os.path.basename(repo_path)}" if repo_path else "/workspace"
        )
        if follow_up:
            prompt = self._build_follow_up_prompt(
                issue,
                plan,
                follow_up_prompt,
                diff_stat,
                workspace_dir,
                input_prompt=input_prompt,
            )
        else:
            prompt = self._build_prompt(issue, plan, workspace_dir, input_prompt=input_prompt)

        try:
            return self._execute_in_docker_workspace(
                repo_path,
                workspace_dir,
                prompt,
                base,
                mode="execution",
                skip_health_checks=skip_health_checks or session is not None,
                session=session,
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                summary=f"OpenHands task failed: {e}",
            )

    def check_runtime_ready(self) -> str | None:
        """Return an error message if Docker or LLM is unavailable."""
        if not self._check_docker_available():
            return "Docker is not available. Start Docker Desktop and retry."
        if not self._check_llm_health():
            return "LLM endpoint not reachable. Start local endpoint first."
        return None

    def _plan_with_local_llm(self, prompt: str) -> str:
        """Run read-only planning via direct chat completion (no OpenHands agent loop)."""
        url = self.llm_base_url.rstrip("/") + "/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.llm_api_key:
            headers["Authorization"] = f"Bearer {self.llm_api_key}"
        model = self._resolve_llm_model_id()
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a senior engineer doing read-only repo analysis. "
                        "Use only the context in the user message. "
                        "Reply in plain markdown with sections "
                        "## Findings, ## Proposed changes, and ## Implementation steps. "
                        "Do not emit tool calls, XML, or shell commands."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 4096,
            "temperature": 0.2,
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=300)
            resp.raise_for_status()
            data = resp.json()
            return (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
        except Exception as exc:
            step(f"  [Planner] Local LLM failed: {exc}")
            return ""

    def _resolve_llm_model_id(self) -> str:
        """Pick a model id that exists on the local OpenAI-compatible server."""
        configured = self.llm_model
        base = self.llm_base_url.rstrip("/")
        headers = {}
        if self.llm_api_key:
            headers["Authorization"] = f"Bearer {self.llm_api_key}"
        try:
            resp = requests.get(f"{base}/models", headers=headers, timeout=5)
            if resp.status_code != 200:
                return configured
            ids = [m["id"] for m in resp.json().get("data", [])]
            if configured in ids:
                return configured
            bare = configured.split("/", 1)[-1]
            if bare in ids:
                return bare
            for mid in ids:
                if bare.lower() in mid.lower() or mid.lower() in configured.lower():
                    return mid
        except Exception:
            pass
        return configured

    @staticmethod
    def _format_task_for_prompt(issue: str, input_prompt: str = "") -> str:
        from agent_graph.state import format_agent_task

        return format_agent_task(issue, input_prompt)

    def _build_planning_prompt(
        self,
        issue: str,
        static_context: str,
        workspace_dir: str,
        *,
        big_picture: str = "",
        input_prompt: str = "",
    ) -> str:
        task = self._format_task_for_prompt(issue, input_prompt)
        overview_block = ""
        if big_picture.strip():
            overview_block = (
                f"\n# Cross-repo context (read first)\n\n{big_picture}\n"
            )
        context_block = ""
        if static_context.strip():
            context_block = f"\n# Repository structure (static scan)\n\n{static_context}\n"
        steps = [
            f"Work in the repository at: {workspace_dir}",
        ]
        if (input_prompt or "").strip():
            steps.append(
                "Follow developer instructions when they narrow or expand scope "
                "beyond the ticket"
            )
        steps.extend(
            [
                "Use `grep -r` / `find` first. Open at most 8 source files with the file editor",
                "Skip tests, mocks, lockfiles, and build artifacts unless directly relevant",
                "**Do not modify any source files.** Read-only analysis only",
                "Identify which files would need changes and why",
                "End with a structured report using exactly these sections",
            ]
        )
        numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
        return (
            f"# Task\n\nIssue: {task}\n"
            f"{overview_block}"
            f"{context_block}\n"
            f"# Instructions (analysis only)\n\n"
            f"{numbered}\n\n"
            f"## Findings\n"
            f"(root cause and relevant files)\n\n"
            f"## Proposed changes\n"
            f"(what to change and where)\n\n"
            f"## Implementation steps\n"
            f"(numbered steps for an engineer to implement)\n"
        )

    def _build_prompt(
        self,
        issue: str,
        plan: str,
        workspace_dir: str,
        *,
        input_prompt: str = "",
    ) -> str:
        task = self._format_task_for_prompt(issue, input_prompt)
        if workspace_dir != "/workspace":
            repo_instruction = f"Work in the repository at: {workspace_dir}"
        else:
            repo_instruction = (
                "No target repository was pre-provided. "
                "If the task mentions a repository, clone it first before working on it."
            )
        steps = [repo_instruction]
        if (input_prompt or "").strip():
            steps.append(
                "Follow developer instructions when they narrow or expand scope "
                "beyond the ticket"
            )
        from agent_graph.branch_naming import branch_creation_instruction

        steps.extend(
            [
                branch_creation_instruction(task),
                "Follow the plan above to implement the changes",
                "Make all modifications inside the repository directory",
                "When finished, run `git diff` and include the output in your final message",
                "Provide a brief summary of what was changed",
            ]
        )
        numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
        return (
            f"# Task\n\nIssue: {task}\n\n"
            f"# Implementation Plan\n\n{plan}\n\n"
            f"# Instructions\n\n"
            f"{numbered}\n\n"
            f"Confirm the repository exists, implement the changes, and show me "
            f"the `git diff` output."
        )

    def _build_follow_up_prompt(
        self,
        issue: str,
        plan: str,
        follow_up_prompt: str,
        diff_stat: str,
        workspace_dir: str,
        *,
        input_prompt: str = "",
    ) -> str:
        from agent_graph.state import format_follow_up_task

        diff_summary = diff_stat.strip()
        task = format_follow_up_task(issue, "", follow_up_prompt, diff_summary)
        if (input_prompt or "").strip():
            task = self._format_task_for_prompt(task, input_prompt)
        if workspace_dir != "/workspace":
            repo_instruction = f"Work in the repository at: {workspace_dir}"
        else:
            repo_instruction = (
                "Continue in the existing work tree. "
                "Do not clone a fresh copy or reset to baseline."
            )
        steps = [
            repo_instruction,
            "Modify the existing implementation; do not revert unrelated changes",
            "Follow the adjustment plan below",
            "Make all modifications inside the repository directory",
            "When finished, run `git diff` and include the output in your final message",
            "Provide a brief summary of what was changed",
        ]
        numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
        return (
            f"# Task\n\n{task}\n\n"
            f"# Adjustment Plan\n\n{plan}\n\n"
            f"# Instructions\n\n"
            f"{numbered}\n\n"
            f"Apply the adjustments on top of the current work tree and show `git diff`."
        )

    @staticmethod
    def authenticated_git_url(repo_url: str) -> str:
        """Backward-compatible alias for repo_clone.authenticated_clone_url."""
        from agent_graph.repo_clone import authenticated_clone_url

        return authenticated_clone_url(repo_url)

    @staticmethod
    def _authenticated_git_url(repo_url: str) -> str:
        return OpenHandsClient.authenticated_git_url(repo_url)

    @staticmethod
    def _detect_platform() -> str:
        machine = platform.machine().lower()
        if "arm" in machine or "aarch64" in machine:
            return "linux/arm64"
        return "linux/amd64"

    @property
    def llm_base_url_in_container(self) -> str:
        return self._llm_base_url_for_container(self.llm_base_url)

    @staticmethod
    def _llm_base_url_for_container(base_url: str) -> str:
        override = os.getenv("LLM_BASE_URL_CONTAINER")
        if override:
            return override
        for host in ("127.0.0.1", "localhost"):
            if host in base_url:
                return base_url.replace(host, "host.docker.internal")
        return base_url

    def _apply_container_llm_env(self) -> dict[str, str | None]:
        previous = {key: os.environ.get(key) for key in LLM_ENV_KEYS}
        os.environ["LLM_MODEL"] = self.llm_model
        os.environ["LLM_API_KEY"] = self.llm_api_key or ""
        os.environ["LLM_BASE_URL"] = self._llm_base_url_for_container(self.llm_base_url)
        return previous

    @staticmethod
    def _restore_env(previous: dict[str, str | None]) -> None:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _check_docker_available(self) -> bool:
        try:
            ok = execute_command(["docker", "version"]).returncode == 0
            if ok:
                verbose_print("  ✓ Docker available")
            else:
                step("  ✗ Docker not available — start Docker Desktop")
            return ok
        except Exception as e:
            step(f"  ✗ Docker check failed: {e}")
            return False

    def _check_llm_health(self, base_url: str | None = None) -> bool:
        url = (base_url or self.llm_base_url).rstrip("/")
        try:
            headers = {}
            if self.llm_api_key:
                headers["Authorization"] = f"Bearer {self.llm_api_key}"
            resp = requests.get(f"{url}/models", headers=headers, timeout=5)
            if resp.status_code != 200:
                step(f"  ✗ LLM health check failed: {resp.status_code}")
            else:
                data = resp.json()
                model_ids = [m["id"] for m in data.get("data", [])]
                verbose_print(
                    f"  ✓ LLM endpoint healthy — {len(model_ids)} models available"
                )
                if any(
                    self.llm_model.lower() in mid.lower() for mid in model_ids
                ):
                    verbose_print(f"  ✓ Target model found ({self.llm_model})")
                else:
                    verbose_print(f"  ⚠ Target model not in list: {model_ids[:5]}")
            return resp.status_code == 200
        except Exception as e:
            step(f"  ✗ LLM health check failed: {e}")
            return False

    def _check_llm_health_in_container(self, workspace: DockerWorkspace) -> bool:
        container_url = self.llm_base_url_in_container.rstrip("/")
        auth = f' -H "Authorization: Bearer {self.llm_api_key}"' if self.llm_api_key else ""
        cmd = f'curl -sf{auth} "{container_url}/models" >/dev/null'
        try:
            result = workspace.execute_command(cmd)
            if result.exit_code == 0:
                verbose_print(f"  ✓ LLM reachable from container ({container_url})")
                return True
            step(
                f"  ✗ LLM not reachable from container ({container_url}), "
                f"exit={result.exit_code}"
            )
            if result.stderr:
                debug(result.stderr.strip())
            return False
        except Exception as e:
            step(f"  ✗ Container LLM health check failed: {e}")
            return False

    def _execute_in_docker_workspace(
        self,
        repo_path: str,
        workspace_dir: str,
        prompt: str,
        baseline_sha: str = "",
        *,
        mode: str = "execution",
        skip_health_checks: bool = False,
        check_changes: bool = True,
        session: AgentServerSession | None = None,
    ) -> ExecutionResult:
        label = "Planner / OpenHands" if mode == "planning" else "Executor / OpenHands"

        if session is not None:
            try:
                if session._workspace is None:
                    session.start()
                return self._execute_on_workspace(
                    session.workspace,
                    repo_path=repo_path,
                    workspace_dir=workspace_dir,
                    prompt=prompt,
                    baseline_sha=baseline_sha,
                    mode=mode,
                    label=label,
                    check_changes=check_changes,
                )
            except ConversationRunError as e:
                return ExecutionResult(
                    success=False,
                    summary=self._format_agent_error(e),
                )
            except Exception as e:
                return ExecutionResult(
                    success=False, summary=self._format_agent_error(e)
                )

        if not skip_health_checks:
            verbose_print(f"\n[{label}] Docker check...")
            if not self._check_docker_available():
                return ExecutionResult(
                    success=False,
                    summary="Docker is not available. Start Docker Desktop and retry.",
                )

            verbose_print(f"\n[{label}] LLM health check...")
            if not self._check_llm_health():
                return ExecutionResult(
                    success=False,
                    summary="LLM endpoint not reachable. Start local endpoint first.",
                )

        volumes: list[str] = []
        if repo_path:
            volumes.append(f"{repo_path}:{workspace_dir}")

        step(f"\n[{label}] Spawning agent-server container...")
        env_backup = self._apply_container_llm_env()
        try:
            with DockerWorkspace(
                server_image=self.server_image,
                platform=self._detect_platform(),
                working_dir=workspace_dir,
                forward_env=list(LLM_ENV_KEYS),
                volumes=volumes,
                detach_logs=False,
            ) as workspace:
                step(f"  Agent server: {workspace.host}")
                return self._execute_on_workspace(
                    workspace,
                    repo_path=repo_path,
                    workspace_dir=workspace_dir,
                    prompt=prompt,
                    baseline_sha=baseline_sha,
                    mode=mode,
                    label=label,
                    check_changes=check_changes,
                    skip_container_health_check=skip_health_checks,
                )
        except ConversationRunError as e:
            return ExecutionResult(
                success=False,
                summary=self._format_agent_error(e),
            )
        except Exception as e:
            return ExecutionResult(success=False, summary=self._format_agent_error(e))
        finally:
            self._restore_env(env_backup)

    def _execute_on_workspace(
        self,
        workspace: DockerWorkspace,
        *,
        repo_path: str,
        workspace_dir: str,
        prompt: str,
        baseline_sha: str = "",
        mode: str,
        label: str,
        check_changes: bool = True,
        skip_container_health_check: bool = False,
    ) -> ExecutionResult:
        max_iterations = (
            self._planner_max_iterations()
            if mode == "planning"
            else self._executor_max_iterations()
        )
        container_llm_url = self.llm_base_url_in_container
        debug(f"  Workspace dir: {workspace_dir}")
        debug(f"  LLM (container): {container_llm_url}")
        step(f"  {label}: {mode} (max_iterations={max_iterations})")

        if not skip_container_health_check and not self._check_llm_health_in_container(
            workspace
        ):
            return ExecutionResult(
                success=False,
                summary=(
                    "LLM not reachable from agent-server container. "
                    "Ensure Omlx is running and LLM_BASE_URL uses localhost "
                    "(rewritten to host.docker.internal in container)."
                ),
            )

        agent_summary = ""
        try:
            llm = LLM(
                model=self.llm_model,
                api_key=self.llm_api_key,
                base_url=container_llm_url,
                num_retries=3,
            )
            agent_tools = [
                Tool(name=TerminalTool.name),
                Tool(name=FileEditorTool.name),
            ]
            agent_kwargs: dict = {
                "llm": llm,
                "tools": agent_tools,
            }
            http_only = os.getenv("OPENHANDS_MCP_HTTP_ONLY", "1").lower() in (
                "1",
                "true",
                "yes",
            )
            mcp_config = build_openhands_mcp_config(http_only=http_only)
            if mcp_config:
                agent_kwargs["mcp_config"] = mcp_config
                filter_regex = os.getenv("OPENHANDS_MCP_FILTER_REGEX", "")
                if filter_regex:
                    agent_kwargs["filter_tools_regex"] = filter_regex
                servers = list(mcp_config.get("mcpServers", {}))
                debug(f"  Agent tools: TerminalTool, FileEditorTool + MCP {servers}")
            else:
                debug("  Agent tools: TerminalTool, FileEditorTool")
            agent = Agent(**agent_kwargs)
            conversation = Conversation(
                agent=agent,
                workspace=workspace,
                max_iteration_per_run=max_iterations,
                stuck_detection=False,
                visualizer=None,
            )
            try:
                step("  Running OpenHands agent...")
                conversation.send_message(prompt)
                conversation.run(blocking=True)
                agent_summary = self._extract_agent_summary(conversation)
            finally:
                try:
                    conversation.close()
                except Exception:
                    pass
        except ConversationRunError as e:
            return ExecutionResult(
                success=False,
                summary=self._format_agent_error(e),
            )
        except Exception as e:
            return ExecutionResult(success=False, summary=self._format_agent_error(e))

        if mode == "planning":
            summary = agent_summary or "Planning analysis completed"
            step(f"  Plan summary: {_summary_preview(summary)}")
            return ExecutionResult(
                success=True,
                summary=summary,
                work_repo_path=repo_path,
                repo_baseline_sha=baseline_sha,
            )

        diff_patch = capture_diff_since(repo_path, baseline_sha) if repo_path else ""
        stat = diff_stat_since(repo_path, baseline_sha) if repo_path else ""
        info = (
            change_summary(repo_path, baseline_sha)
            if repo_path and baseline_sha
            else {}
        )
        if info:
            step(
                f"  Changes: uncommitted={info['uncommitted']}, "
                f"commits={info['commits_since_baseline']}"
            )
            if stat:
                for line in stat.splitlines()[:5]:
                    debug(f"    {line}")

        repo_label = os.path.basename(repo_path) if repo_path else "workspace"
        if (
            check_changes
            and repo_path
            and baseline_sha
            and not has_changes_since(repo_path, baseline_sha)
        ):
            summary = (
                f"No file changes in {repo_label} — OpenHands finished without editing the repo"
            )
            step(f"  ⚠ {summary}")
            return ExecutionResult(
                success=True,
                no_changes=True,
                summary=summary,
                work_repo_path=repo_path,
                repo_baseline_sha=baseline_sha,
                diff_patch=diff_patch,
                change_stat=stat,
            )

        summary = agent_summary or f"Task completed: {repo_label}"
        step(f"  Done: {_summary_preview(summary)}")
        return ExecutionResult(
            success=True,
            summary=summary,
            work_repo_path=repo_path,
            repo_baseline_sha=baseline_sha,
            diff_patch=diff_patch,
            change_stat=stat,
        )

    @staticmethod
    def _format_agent_error(exc: Exception) -> str:
        if isinstance(exc, ConversationRunError):
            inner = exc.original_exception
            msg = str(inner).strip() if inner else str(exc).strip()
        else:
            msg = str(exc).strip()
        if not msg:
            msg = "unknown agent failure"
        if "Remote conversation ended with error" in msg:
            return (
                "Agent error: OpenHands conversation failed on the agent server "
                "(no detailed error event). Check Docker logs for LLM or tool errors."
            )
        if "Prompt too long" in msg or "context window" in msg.lower():
            return (
                f"Agent error: LLM context limit exceeded — {msg}. "
                "Try lowering PLANNER_STATIC_CONTEXT_MAX_CHARS or use a larger-context model."
            )
        if msg.startswith("Agent error:"):
            return msg
        return f"Agent error: {msg}"

    @staticmethod
    def _extract_agent_summary(conversation: Conversation) -> str:
        try:
            events = getattr(conversation, "events", None) or []
            for event in reversed(list(events)):
                role = getattr(event, "role", None) or getattr(event, "source", None)
                content = getattr(event, "content", None) or getattr(event, "message", None)
                if content and str(role).lower() in ("assistant", "agent"):
                    text = content if isinstance(content, str) else str(content)
                    if text.strip():
                        return text.strip()
        except Exception:
            pass
        return ""


def _summary_preview(text: str, max_len: int = 200) -> str:
    line = (text or "").strip().split("\n")[0]
    if len(line) > max_len:
        return line[: max_len - 3] + "..."
    return line
