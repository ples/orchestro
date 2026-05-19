"""OpenHands client: spawns a per-task agent-server container via DockerWorkspace."""

import os
import platform
import subprocess

import requests
from openhands.sdk import LLM, Agent, Conversation, Tool
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
    rev_parse,
)
from agent_graph.models import ExecutionResult

DEFAULT_SERVER_IMAGE = "ghcr.io/openhands/agent-server:1.21.1-python"
LLM_ENV_KEYS = ("LLM_MODEL", "LLM_API_KEY", "LLM_BASE_URL")


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

    def _ensure_repo_in_agent(self, target_repo: str) -> tuple[str, str]:
        """Clone or copy a target repo into a temp directory on the host.

        Returns:
            (repo_path, baseline_sha) where baseline_sha is HEAD after setup.
        """
        tmp_dir = subprocess.check_output(
            ["mktemp", "-d", "-t", "openhands_repo_"], text=True
        ).strip()

        if target_repo:
            is_remote = (
                target_repo.startswith("http://")
                or target_repo.startswith("https://")
                or target_repo.startswith("git@")
            )
            if is_remote:
                repo_name = target_repo.rstrip("/").split("/")[-1].replace(".git", "")
                repo_path = os.path.join(tmp_dir, repo_name)
                clone_url = self._authenticated_git_url(target_repo)
                subprocess.run(
                    ["git", "clone", "--depth", "1", clone_url, repo_path],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "-C", repo_path, "add", "."],
                    check=False,
                    capture_output=True,
                )
                subprocess.run(
                    [
                        "git",
                        "-C",
                        repo_path,
                        "commit",
                        "-m",
                        "initial",
                        "--allow-empty-message",
                    ],
                    check=False,
                    capture_output=True,
                )
            else:
                if not os.path.isdir(target_repo):
                    raise ExecutorError(f"Target repo not found: {target_repo}")
                repo_name = os.path.basename(target_repo)
                repo_path = os.path.join(tmp_dir, repo_name)
                subprocess.run(
                    ["cp", "-R", f"{target_repo}/.", repo_path],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "-C", repo_path, "add", "."],
                    check=False,
                    capture_output=True,
                )
                subprocess.run(
                    [
                        "git",
                        "-C",
                        repo_path,
                        "commit",
                        "-m",
                        "initial",
                        "--allow-empty-message",
                    ],
                    check=False,
                    capture_output=True,
                )
            baseline = rev_parse(repo_path)
            return repo_path, baseline

        return "", ""

    def run_task(
        self,
        target_repo: str,
        issue: str,
        plan: str,
    ) -> ExecutionResult:
        repo_path, baseline_sha = self._ensure_repo_in_agent(target_repo)
        workspace_dir = (
            f"/workspace/{os.path.basename(repo_path)}" if repo_path else "/workspace"
        )
        prompt = self._build_prompt(issue, plan, workspace_dir)

        try:
            return self._execute_in_docker_workspace(
                repo_path, workspace_dir, prompt, baseline_sha
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                summary=f"OpenHands task failed: {e}",
            )

    def _build_prompt(
        self, issue: str, plan: str, workspace_dir: str
    ) -> str:
        if workspace_dir != "/workspace":
            repo_instruction = f"Work in the repository at: {workspace_dir}"
        else:
            repo_instruction = (
                "No target repository was pre-provided. "
                "If the task mentions a repository, clone it first before working on it."
            )
        return (
            f"# Task\n\nIssue: {issue}\n\n"
            f"# Implementation Plan\n\n{plan}\n\n"
            f"# Instructions\n\n"
            f"1. {repo_instruction}\n"
            f"2. Follow the plan above to implement the changes.\n"
            f"3. Make all modifications inside the repository directory.\n"
            f"4. When finished, run `git diff` and include the output in your final message.\n"
            f"5. Provide a brief summary of what was changed.\n\n"
            f"Confirm the repository exists, implement the changes, and show me "
            f"the `git diff` output."
        )

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

    @staticmethod
    def _authenticated_git_url(repo_url: str) -> str:
        if not repo_url.startswith("https://github.com/"):
            return repo_url
        token = os.getenv("GITHUB_TOKEN", "")
        if not token or "@" in repo_url:
            return repo_url
        return repo_url.replace(
            "https://github.com/",
            f"https://x-access-token:{token}@github.com/",
            1,
        )

    def _apply_container_llm_env(self) -> dict[str, str | None]:
        """Set LLM_* env vars forwarded into the agent-server container."""
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
                print("  ✓ Docker available")
            else:
                print("  ✗ Docker not available — start Docker Desktop")
            return ok
        except Exception as e:
            print(f"  ✗ Docker check failed: {e}")
            return False

    def _check_llm_health(self, base_url: str | None = None) -> bool:
        url = (base_url or self.llm_base_url).rstrip("/")
        try:
            headers = {}
            if self.llm_api_key:
                headers["Authorization"] = f"Bearer {self.llm_api_key}"
            resp = requests.get(f"{url}/models", headers=headers, timeout=5)
            if resp.status_code != 200:
                print(f"  ✗ LLM health check failed: {resp.status_code}")
            else:
                data = resp.json()
                model_ids = [m["id"] for m in data.get("data", [])]
                print(f"  ✓ LLM endpoint healthy — {len(model_ids)} models available")
                if any(
                    self.llm_model.lower() in mid.lower() for mid in model_ids
                ):
                    print(f"  ✓ Target model found ({self.llm_model})")
                else:
                    print(f"  ⚠ Target model not in list: {model_ids[:5]}")
            return resp.status_code == 200
        except Exception as e:
            print(f"  ✗ LLM health check failed: {e}")
            return False

    def _check_llm_health_in_container(self, workspace: DockerWorkspace) -> bool:
        container_url = self.llm_base_url_in_container.rstrip("/")
        auth = f' -H "Authorization: Bearer {self.llm_api_key}"' if self.llm_api_key else ""
        cmd = f'curl -sf{auth} "{container_url}/models" >/dev/null'
        try:
            result = workspace.execute_command(cmd)
            if result.exit_code == 0:
                print(f"  ✓ LLM reachable from container ({container_url})")
                return True
            print(
                f"  ✗ LLM not reachable from container ({container_url}), "
                f"exit={result.exit_code}"
            )
            if result.stderr:
                print(f"    {result.stderr.strip()}")
            return False
        except Exception as e:
            print(f"  ✗ Container LLM health check failed: {e}")
            return False

    def _execute_in_docker_workspace(
        self,
        repo_path: str,
        workspace_dir: str,
        prompt: str,
        baseline_sha: str = "",
    ) -> ExecutionResult:
        print("\n[Executor / OpenHands]\nDocker check...")
        if not self._check_docker_available():
            return ExecutionResult(
                success=False,
                summary="Docker is not available. Start Docker Desktop and retry.",
            )

        print("\n[Executor / OpenHands]\nLLM health check...")
        if not self._check_llm_health():
            return ExecutionResult(
                success=False,
                summary="LLM endpoint not reachable. Start local endpoint first.",
            )

        print("\n[Executor / OpenHands]\nSpawning agent-server container...")
        volumes: list[str] = []
        if repo_path:
            volumes.append(f"{repo_path}:{workspace_dir}")

        env_backup = self._apply_container_llm_env()
        container_llm_url = self.llm_base_url_in_container
        try:
            with DockerWorkspace(
                server_image=self.server_image,
                platform=self._detect_platform(),
                working_dir=workspace_dir,
                forward_env=list(LLM_ENV_KEYS),
                volumes=volumes,
            ) as workspace:
                print(f"  Agent server: {workspace.host}")
                print(f"  Workspace: {workspace.working_dir}")
                print(f"  LLM (container): {container_llm_url}")

                if not self._check_llm_health_in_container(workspace):
                    return ExecutionResult(
                        success=False,
                        summary=(
                            "LLM not reachable from agent-server container. "
                            "Ensure Omlx is running and LLM_BASE_URL uses localhost "
                            "(rewritten to host.docker.internal in container)."
                        ),
                    )

                llm = LLM(
                    model=self.llm_model,
                    api_key=self.llm_api_key,
                    base_url=container_llm_url,
                    num_retries=3,
                )
                agent = Agent(
                    llm=llm,
                    tools=[
                        Tool(name=TerminalTool.name),
                        Tool(name=FileEditorTool.name),
                    ],
                )
                print("  Agent tools: TerminalTool, FileEditorTool")
                conversation = Conversation(
                    agent=agent,
                    workspace=workspace,
                    max_iteration_per_run=500,
                    stuck_detection=False,
                )
                try:
                    print("  Sending prompt to agent...")
                    conversation.send_message(prompt)
                    conversation.run(blocking=True)
                    print("  Agent completed")
                finally:
                    try:
                        conversation.close()
                    except Exception:
                        pass
        except Exception as e:
            return ExecutionResult(success=False, summary=f"Agent error: {e}")
        finally:
            self._restore_env(env_backup)

        diff_patch = capture_diff_since(repo_path, baseline_sha) if repo_path else ""
        stat = diff_stat_since(repo_path, baseline_sha) if repo_path else ""
        info = (
            change_summary(repo_path, baseline_sha)
            if repo_path and baseline_sha
            else {}
        )
        if info:
            print(
                f"  Changes: uncommitted={info['uncommitted']}, "
                f"commits={info['commits_since_baseline']}, "
                f"baseline={info['baseline_sha']}"
            )
            if stat:
                for line in stat.splitlines()[:5]:
                    print(f"    {line}")

        label = os.path.basename(repo_path) if repo_path else "workspace"
        if repo_path and baseline_sha and not has_changes_since(repo_path, baseline_sha):
            summary = (
                f"No file changes in {label} — OpenHands finished without editing the repo"
            )
            print(f"  ⚠ {summary}")
            return ExecutionResult(
                success=False,
                summary=summary,
                work_repo_path=repo_path,
                repo_baseline_sha=baseline_sha,
                diff_patch=diff_patch,
                change_stat=stat,
            )

        summary = f"Task completed: {label}"
        print(f"  Done: {summary}")
        return ExecutionResult(
            success=True,
            summary=summary,
            work_repo_path=repo_path,
            repo_baseline_sha=baseline_sha,
            diff_patch=diff_patch,
            change_stat=stat,
        )
