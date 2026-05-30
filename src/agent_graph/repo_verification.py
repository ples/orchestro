"""Run repo-local verification commands (tests/lint)."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass

from agent_graph.models import VerificationResult


def verifier_run_tests() -> bool:
    raw = os.getenv("VERIFIER_RUN_TESTS", "1").strip().lower()
    return raw not in ("0", "false", "no")


def verifier_skip_lint() -> bool:
    raw = os.getenv("VERIFIER_SKIP_LINT", "0").strip().lower()
    return raw in ("1", "true", "yes")


def verifier_timeout_sec() -> int:
    raw = os.getenv("VERIFIER_TIMEOUT_SEC", "300").strip()
    if raw.isdigit():
        return int(raw)
    return 300


def verifier_bootstrap_deps() -> bool:
    raw = os.getenv("VERIFIER_BOOTSTRAP_DEPS", "0").strip().lower()
    return raw in ("1", "true", "yes")


@dataclass
class RepoCheckResult:
    passed: bool
    command: str
    details: str


def run_checks(work_repo_path: str) -> RepoCheckResult:
    if not verifier_run_tests():
        return RepoCheckResult(passed=True, command="", details="tests disabled")
    if not work_repo_path or not os.path.isdir(work_repo_path):
        return RepoCheckResult(passed=False, command="", details="no work repo")

    commands = _detect_commands(work_repo_path)
    if not commands:
        return RepoCheckResult(
            passed=True,
            command="",
            details="no test/lint commands detected",
        )

    if _is_node_repo(work_repo_path):
        bootstrap_err = _ensure_node_dependencies(work_repo_path)
        if bootstrap_err:
            return RepoCheckResult(
                passed=False,
                command="bootstrap",
                details=bootstrap_err,
            )

    failures: list[str] = []
    ran: list[str] = []
    for cmd, label in commands:
        ran.append(label)
        ok, tail = _run_command(work_repo_path, cmd)
        if not ok:
            failures.append(f"{label} failed:\n{tail}")

    if failures:
        return RepoCheckResult(
            passed=False,
            command=", ".join(ran),
            details="\n\n".join(failures),
        )
    return RepoCheckResult(
        passed=True,
        command=", ".join(ran),
        details="checks passed",
    )


def to_verification_result(check: RepoCheckResult) -> VerificationResult:
    return VerificationResult(
        passed=check.passed,
        details=check.details if check.passed else f"{check.command}: {check.details}",
    )


def _detect_commands(repo_path: str) -> list[tuple[list[str], str]]:
    pkg = os.path.join(repo_path, "package.json")
    if os.path.isfile(pkg):
        return _node_commands(pkg)
    if os.path.isfile(os.path.join(repo_path, "pyproject.toml")) or os.path.isfile(
        os.path.join(repo_path, "setup.py")
    ):
        return [(["pytest", "-q"], "pytest")]
    if os.path.isfile(os.path.join(repo_path, "pom.xml")):
        return [(["mvn", "-q", "test", "-DskipTests=false"], "mvn test")]
    return []


def _is_node_repo(repo_path: str) -> bool:
    return os.path.isfile(os.path.join(repo_path, "package.json"))


def _node_commands(package_json_path: str) -> list[tuple[list[str], str]]:
    try:
        with open(package_json_path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    scripts = data.get("scripts") or {}
    commands: list[tuple[list[str], str]] = []
    if "test" in scripts:
        commands.append((["npm", "test", "--", "--passWithNoTests"], "npm test"))
    if not verifier_skip_lint() and "lint" in scripts:
        commands.append((["npm", "run", "lint"], "npm run lint"))
    return commands


def _run_command(cwd: str, cmd: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=verifier_timeout_sec(),
        )
    except subprocess.TimeoutExpired:
        return False, f"timeout after {verifier_timeout_sec()}s"
    except OSError as exc:
        return False, str(exc)
    tail = (result.stderr or result.stdout or "")[-2000:]
    return result.returncode == 0, tail


def _ensure_node_dependencies(repo_path: str) -> str:
    node_modules = os.path.join(repo_path, "node_modules")
    if os.path.isdir(node_modules):
        return ""

    install_cmd = _node_install_command(repo_path)
    if not install_cmd:
        return ""

    if not verifier_bootstrap_deps():
        return (
            "missing JS dependencies: node_modules not found. "
            "Enable VERIFIER_BOOTSTRAP_DEPS=1 to auto-install before checks."
        )

    ok, tail = _run_command(repo_path, install_cmd)
    if ok:
        return ""
    return f"dependency bootstrap failed ({' '.join(install_cmd)}):\n{tail}"


def _node_install_command(repo_path: str) -> list[str]:
    if os.path.isfile(os.path.join(repo_path, "pnpm-lock.yaml")):
        return ["pnpm", "install", "--frozen-lockfile"]
    if os.path.isfile(os.path.join(repo_path, "package-lock.json")):
        return ["npm", "ci"]
    if os.path.isfile(os.path.join(repo_path, "yarn.lock")):
        return ["yarn", "install", "--frozen-lockfile"]
    return []
