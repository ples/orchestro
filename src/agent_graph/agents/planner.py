import os
import re
import subprocess
from pathlib import Path

from agent_graph.agents.repo_detector import RepoDetectorAgent
from agent_graph.exceptions import ExecutorError
from agent_graph.openhands_client import (
    AgentServerSession,
    OpenHandsClient,
    reuse_agent_server,
)
from agent_graph.repo_clone import clone_planner_workspace
from agent_graph.state import RepoRecord, TaskState, format_agent_task

from .base import BaseAgent

# Source file extensions supported by the dependency analyser
SOURCE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".rb", ".php"}

# Common entry-point file / module names
ENTRY_POINT_NAMES = {"main", "app", "index", "__main__"}
DEFAULT_STATIC_CONTEXT_MAX_CHARS = 8000


class PlannerAgent(BaseAgent):
    """Plans implementation for a given issue."""

    name = "planner"

    def _execute(self, state: TaskState) -> dict:
        issue = state["issue"]
        input_prompt = state.get("input_prompt", "")
        agent_task = format_agent_task(issue, input_prompt)
        print("\n[Planner]")
        print(f"Analyzing issue: {issue}")
        if input_prompt.strip():
            preview = input_prompt.strip()[:120]
            suffix = "..." if len(input_prompt.strip()) > 120 else ""
            print(f"  Developer instructions: {preview}{suffix}")

        mcp_context = state.get("mcp_context", "")
        mcp_tools_used: list[str] = list(state.get("mcp_tools_used", []))
        if not mcp_context:
            mcp_context, tools = self._fetch_mcp_context(agent_task)
            mcp_tools_used.extend(tools)

        github_url = state.get("github_issue_url", "")
        repo_info = self._parse_repo_url(github_url) if github_url else ""

        target_repos: list[RepoRecord] = list(state.get("target_repos", []))
        if target_repos:
            for r in target_repos:
                print(f"  [Planner] Target repo: {r.get('target_repo_path', '')}")

        if not target_repos:
            det = RepoDetectorAgent()
            target_repos = det.run(state).get("target_repos", [])

        for r in target_repos:
            print(f"  [Planner] Detected repo: {r.get('target_repo_path', '')}")

        repo_context_parts: list[str] = []
        if mcp_context:
            repo_context_parts.append(mcp_context)

        if target_repos:
            target_repos = self._analyze_target_repos(
                issue, input_prompt, target_repos, repo_context_parts
            )
        else:
            print("  [Planner] No target repositories — building plan from issue text only")
            repo_context_parts.append(
                "(No repositories detected — planner working without repo context)"
            )

        repo_context = "\n\n".join(repo_context_parts)
        plan = self._build_plan(
            issue, repo_info, repo_context, target_repos, input_prompt=input_prompt
        )

        return {
            "plan": plan,
            "repo_context": repo_context,
            "target_repos": target_repos,
            "mcp_context": mcp_context,
            "mcp_tools_used": mcp_tools_used,
            "workflow_node": "planning",
        }

    def _analyze_target_repos(
        self,
        issue: str,
        input_prompt: str,
        target_repos: list[RepoRecord],
        repo_context_parts: list[str],
    ) -> list[RepoRecord]:
        agent_task = format_agent_task(issue, input_prompt)
        client = OpenHandsClient()
        runtime_err = client.check_runtime_ready()
        skip_checks = runtime_err is None

        urls = [
            r.get("target_repo_path", "")
            for r in target_repos
            if r.get("target_repo_path", "")
        ]

        updated: list[RepoRecord] = []
        if not urls:
            return list(target_repos)

        print(f"\n  [Planner] Phase 1: cloning {len(urls)} repositories...")
        try:
            _parent, clones = clone_planner_workspace(urls)
        except ExecutorError as exc:
            for repo_record in target_repos:
                url = repo_record.get("target_repo_path", "")
                record: RepoRecord = dict(repo_record)
                if url:
                    record["repo_summary"] = f"Clone failed: {exc}"
                updated.append(record)
            return updated

        prepared: list[tuple[RepoRecord, str, str, str]] = []
        for repo_record in target_repos:
            url = repo_record.get("target_repo_path", "")
            if not url:
                updated.append(repo_record)
                continue
            record = dict(repo_record)
            if url not in clones:
                record["repo_summary"] = "Clone failed: repository not in workspace"
                updated.append(record)
                continue
            clone_path, baseline = clones[url]
            record["planner_clone_path"] = clone_path
            record["repo_baseline_sha"] = baseline
            prepared.append((record, url, clone_path, baseline))

        if not prepared:
            return updated

        print(f"\n  [Planner] Phase 2: scanning {len(prepared)} repositories...")
        repo_scans: dict[str, str] = {}
        for record, url, clone_path, _baseline in prepared:
            static_context = self._analyse_repo(clone_path)
            grep_hints = self._collect_grep_hints(clone_path, agent_task)
            if grep_hints:
                static_context = f"{static_context}\n\n{grep_hints}"
            repo_scans[url] = static_context
            repo_context_parts.append(f"### Static scan: {url}\n{static_context}")

        combined_context = self._build_combined_repo_context(prepared, repo_scans)
        print("\n  [Planner] Phase 3: cross-repo analysis (big picture)...")
        big_picture = client.run_cross_repo_planning(
            issue, combined_context, input_prompt=input_prompt
        )
        if big_picture:
            repo_context_parts.insert(0, f"### Cross-repo overview\n{big_picture}")

        use_openhands = os.getenv("PLANNER_USE_OPENHANDS", "").lower() in (
            "1",
            "true",
            "yes",
        )
        session: AgentServerSession | None = None
        if (
            use_openhands
            and reuse_agent_server()
            and not runtime_err
            and len(prepared) > 0
        ):
            mounts = [
                (clone_path, os.path.basename(clone_path))
                for _record, _url, clone_path, _base in prepared
            ]
            session = AgentServerSession(client, mounts, skip_health_checks=skip_checks)
            try:
                session.start()
            except Exception as exc:
                print(f"  [Planner] Shared agent server failed: {exc}")
                session = None

        print(f"\n  [Planner] Phase 4: per-repository analysis ({len(prepared)} repos)...")
        try:
            for i, (record, url, clone_path, baseline) in enumerate(prepared):
                print(f"\n  [Planner] Repo {i + 1}/{len(prepared)}: {url}")

                if runtime_err:
                    record["repo_summary"] = f"Planning skipped: {runtime_err}"
                    updated.append(record)
                    continue

                result = client.run_planning_task(
                    target_repo=url,
                    issue=issue,
                    static_context=repo_scans[url],
                    existing_repo_path=clone_path,
                    baseline_sha=baseline,
                    skip_health_checks=skip_checks,
                    big_picture=big_picture,
                    input_prompt=input_prompt,
                    session=session,
                )

                if result.success:
                    record["repo_summary"] = result.summary
                    print(f"  [Planner] Analysis complete for {url}")
                else:
                    record["repo_summary"] = (
                        result.summary or "Planning analysis failed"
                    )
                    print(
                        f"  [Planner] Analysis failed for {url}: "
                        f"{record['repo_summary'][:120]}"
                    )

                if result.planner_clone_path:
                    record["planner_clone_path"] = result.planner_clone_path
                if result.repo_baseline_sha:
                    record["repo_baseline_sha"] = result.repo_baseline_sha

                updated.append(record)
        finally:
            if session is not None:
                session.stop()

        return updated

    @staticmethod
    def _build_combined_repo_context(
        prepared: list[tuple[RepoRecord, str, str, str]],
        repo_scans: dict[str, str],
    ) -> str:
        parts = []
        for _record, url, clone_path, _baseline in prepared:
            name = os.path.basename(clone_path)
            scan = repo_scans.get(url, "")
            parts.append(f"## {name}\n- URL: {url}\n- Path: /workspace/{name}\n\n{scan}")
        return "\n\n".join(parts)

    @staticmethod
    def _fetch_mcp_context(issue: str) -> tuple[str, list[str]]:
        try:
            from agent_graph.mcp.context7 import enrich_with_context7_sync

            return enrich_with_context7_sync(issue)
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            print(f"  [MCP] Context7 skipped: {exc}")
            return "", []

    def _analyse_repo(self, repo_dir: str) -> str:
        """Walk the repo and produce a dependency-graph string."""
        repo_path = Path(repo_dir)

        print(f"  [Repo] Scanning {repo_path} for source files...")

        # 1. Collect all source files
        source_files: list[str] = []
        for ext in SOURCE_EXTENSIONS:
            source_files.extend(str(p) for p in repo_path.rglob(f"*{ext}"))

        ext_counts: dict[str, int] = {}
        for fp in source_files:
            ext = os.path.splitext(fp)[1]
            ext_counts[ext] = ext_counts.get(ext, 0) + 1

        print(f"  [Repo] Found {len(source_files)} source files ({', '.join(f'{v}{k}' for k, v in ext_counts.items())})")

        if not source_files:
            # Fallback: list the top-level tree
            tree_lines = [str(f.relative_to(repo_path)) for f in sorted(repo_path.iterdir())]
            print(f"  [Repo] No source files — falling back to directory tree ({len(tree_lines)} entries)")
            return "\nRepo tree (no source files found):\n" + "\n".join(f"  {file}" for file in tree_lines)

        # 2. Build dependency map: file -> [imported local files]
        dep_map: dict[str, list[str]] = {}
        all_imported: set[str] = set()

        total_parsed = 0
        total_imports_found = 0
        for fpath in source_files:
            rel = os.path.relpath(fpath, repo_path)
            imports = self._extract_imports(fpath, repo_path)
            dep_map[rel] = imports
            total_parsed += 1
            if imports:
                total_imports_found += len(imports)
            for imp in imports:
                all_imported.add(imp)

        print(f"  [Repo] Parsed {total_parsed} files, found {total_imports_found} local imports")

        # 3. Derive entry points (files with no incoming local imports)
        entry_points = [f for f in dep_map if f not in all_imported]
        entry_points.sort(key=lambda f: (0 if Path(f).stem in ENTRY_POINT_NAMES else 1, f))
        print(f"  [Repo] Detected {len(entry_points)} entry point(s)")

        # 4. Compute depth level via BFS from entry points
        depth_map: dict[str, int] = {}
        reverse_deps: dict[str, list[str]] = {f: [] for f in dep_map}
        for f, deps in dep_map.items():
            for d in deps:
                if d in reverse_deps:
                    reverse_deps[d].append(f)

        queue: list[tuple[str, int]] = [
            (e, 0) for e in entry_points
        ]
        # Also add modules with no deps at all as depth-0
        for f, deps in dep_map.items():
            if not deps and f not in entry_points:
                queue.append((f, 0))

        visited: set[str] = set()
        while queue:
            current, d = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            depth_map[current] = d
            for dep in reverse_deps.get(current, []):
                if dep not in visited:
                    queue.append((dep, d + 1))

        max_depth = max(depth_map.values()) if depth_map else 0
        for f in dep_map:
            if f not in depth_map:
                depth_map[f] = max_depth + 1

        print(f"  [Repo] Max dependency depth: {max_depth}")

        lines = self._format_repo_context(
            repo_path,
            source_files,
            ext_counts,
            dep_map,
            entry_points,
            depth_map,
            total_imports_found,
        )
        return self._truncate_static_context("\n".join(lines))

    @staticmethod
    def _static_context_max_chars() -> int:
        raw = os.getenv("PLANNER_STATIC_CONTEXT_MAX_CHARS", "").strip()
        if raw.isdigit():
            return int(raw)
        return DEFAULT_STATIC_CONTEXT_MAX_CHARS

    @staticmethod
    def _truncate_static_context(text: str) -> str:
        limit = PlannerAgent._static_context_max_chars()
        if len(text) <= limit:
            return text
        suffix = "\n\n[... static context truncated for LLM context limit ...]"
        keep = max(0, limit - len(suffix))
        return text[:keep] + suffix

    def _format_repo_context(
        self,
        repo_path: Path,
        source_files: list[str],
        ext_counts: dict[str, int],
        dep_map: dict[str, list[str]],
        entry_points: list[str],
        depth_map: dict[str, int],
        total_imports_found: int,
    ) -> list[str]:
        lines = ["=== Repository Context ===", ""]
        ext_summary = ", ".join(f"{v}{k}" for k, v in sorted(ext_counts.items()))
        lines.append(
            f"Summary: {len(source_files)} source files ({ext_summary}), "
            f"{total_imports_found} resolved local imports"
        )

        compact = total_imports_found == 0 and len(source_files) > 40
        if compact:
            lines.append("")
            lines.append(self._summarize_file_tree(repo_path, source_files))
            lines.append(
                "\nHint: start with targeted grep for issue keywords "
                "(email, verified, verification, status) before opening files."
            )
        else:
            lines.append("")
            lines.append("File tree (source files):")
            for tf in sorted(dep_map.keys())[:120]:
                parts = Path(tf).parts
                indent = "  " * (len(parts) - 1)
                lines.append(f"{indent}{parts[-1]}")
            if len(dep_map) > 120:
                lines.append(f"  ... and {len(dep_map) - 120} more files")

        if entry_points and not compact:
            lines.append("\nEntry points (no internal dependencies):")
            for ep in entry_points[:30]:
                lines.append(f"  - {Path(ep).with_suffix('')}")
            if len(entry_points) > 30:
                lines.append(f"  ... and {len(entry_points) - 30} more")

        if depth_map and total_imports_found > 0:
            mx_d = max(depth_map.values())
            for d in range(mx_d + 1):
                mods = sorted(f for f, dep in depth_map.items() if dep == d)
                if not mods:
                    continue
                lines.append(f"\nLayer {d} (depth {d}):")
                for m in mods[:40]:
                    imp_str = ", ".join(dep_map[m]) if dep_map[m] else "(standalone)"
                    lines.append(f"  - {m} => [{imp_str}]")
                if len(mods) > 40:
                    lines.append(f"  ... and {len(mods) - 40} more")

        lines.extend(["", "=== End Repository Context ==="])
        return lines

    def _collect_grep_hints(self, repo_dir: str, issue: str) -> str:
        keywords = [
            "email",
            "verified",
            "verification",
            "Unknown",
            "status",
        ]
        for word in re.findall(r"[A-Za-z]{5,}", issue):
            if word.lower() not in {k.lower() for k in keywords}:
                keywords.append(word)
        keywords = list(dict.fromkeys(keywords))[:10]

        patterns: list[str] = []
        for kw in keywords:
            try:
                result = subprocess.run(
                    [
                        "grep",
                        "-rni",
                        "--include=*.java",
                        "--include=*.ts",
                        "--include=*.tsx",
                        "--include=*.js",
                        "-m",
                        "2",
                        kw,
                        repo_dir,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except (subprocess.TimeoutExpired, OSError):
                continue
            if result.stdout.strip():
                patterns.append(f"### grep '{kw}'\n```\n{result.stdout.strip()[:2000]}\n```")

        if not patterns:
            return ""
        body = "\n\n".join(patterns[:6])
        if len(patterns) > 6:
            body += f"\n\n(... {len(patterns) - 6} more keyword searches omitted)"
        return f"=== Targeted grep hints ===\n\n{body}\n\n=== End grep hints ==="

    @staticmethod
    def _summarize_file_tree(repo_path: Path, source_files: list[str]) -> str:
        by_top: dict[str, int] = {}
        for fpath in source_files:
            rel = os.path.relpath(fpath, repo_path)
            top = rel.split(os.sep)[0] if os.sep in rel else rel
            by_top[top] = by_top.get(top, 0) + 1
        lines = ["File tree summary (dependency scan skipped large listing):"]
        for top, count in sorted(by_top.items(), key=lambda x: (-x[1], x[0]))[:25]:
            lines.append(f"  - {top}/ ({count} files)")
        if len(by_top) > 25:
            lines.append(f"  ... and {len(by_top) - 25} more top-level paths")
        return "\n".join(lines)

    def _extract_imports(self, filepath: str, repo_path: Path) -> list[str]:
        """Parse local imports from a source file and resolve them."""
        imports: set[str] = set()
        try:
            with open(filepath, errors="ignore") as f:
                content = f.read(2_000_000)
        except OSError:
            return []

        for line in content.splitlines():
            stripped = line.strip()

            # Python:  import foo  /  from foo import bar
            py_match = re.match(r"^(?:from|import)\s+([\w.]+)", stripped)
            if py_match:
                mod = py_match.group(1)
                imports.update(self._resolve_python_import(mod))
                continue

            # JS/TS:  import ... from '...'  or  import('...')
            ts_match = re.search(r"""(?:from\s+|require\s*\()?\s*['"]([^'"]+)['"]""", stripped)
            if ts_match:
                imp_path = ts_match.group(1)
                if imp_path.startswith(".") and "." not in imp_path.split("/")[0]:
                    imports.update(self._resolve_ts_import(imp_path, filepath, repo_path))

        # Resolve relative to repo_path
        result = []
        for imp in imports:
            abs_imp = os.path.abspath(imp)
            try:
                rel = os.path.relpath(abs_imp, repo_path)
                if not rel.startswith(".."):
                    result.append(rel)
            except ValueError:
                pass

        return result

    def _resolve_python_import(self, mod: str) -> set[str]:
        hits: set[str] = set()
        possible = [mod.replace(".", "/") + ".py", mod.replace(".", "/") + "/__init__.py"]
        for p in possible:
            if os.path.isfile(p):
                hits.add(p)
        return hits

    def _resolve_ts_import(self, imp: str, origin: str, repo_path: str) -> set[str]:
        hits: set[str] = set()
        candidates = [
            imp,
            imp + ".js",
            imp + ".ts",
            imp + ".tsx",
            imp + ".jsx",
            imp + "/index.js",
            imp + "/index.ts",
        ]
        for c in candidates:
            if os.path.isfile(c):
                hits.add(c)
        return hits

    @staticmethod
    def _update_repo_context(repo_context: str, target_repos: list | None = None) -> str:
        """Append repo list to existing repo_context."""
        if not target_repos:
            return repo_context
        lines = []
        lines.append("## Target Repositories")
        for r in target_repos:
            url = r.get("target_repo_path", "") if isinstance(r, dict) else r
            lines.append(f"- {url}")
        lines.append("")
        return repo_context + "\n" + "\n".join(lines)

    # ------------------------------------------------------------------

    def _parse_repo_url(self, url: str) -> str:
        """Extract owner/repo from a GitHub URL."""
        patterns = [
            r"https://github\.com/([^/]+)/([^/]+)",
            r"github:([^/]+)/([^/]+)",
        ]
        for pattern in patterns:
            match = re.match(pattern, url)
            if match:
                return f"{match.group(1)}/{match.group(2)}"
        return ""

    def _build_plan(
        self,
        issue: str,
        repo_info: str,
        repo_context: str,
        target_repos: list[RepoRecord] | None = None,
        *,
        input_prompt: str = "",
    ) -> str:
        """Generate aggregated implementation plan from per-repo analysis."""
        lines = ["=== PLAN ===", ""]
        if (input_prompt or "").strip():
            lines.append("## Developer instructions")
            lines.append(input_prompt.strip())
            lines.append("")

        if repo_context.strip():
            lines.append(repo_context)
            lines.append("")

        if target_repos:
            lines.append(f"## Target repositories ({len(target_repos)})")
            for r in target_repos:
                url = r.get("target_repo_path", "")
                lines.append(f"  - {url}")
            lines.append("")
            lines.append("## Per-repository analysis")
            for r in target_repos:
                url = r.get("target_repo_path", "")
                summary = r.get("repo_summary", "").strip()
                if url:
                    lines.append(f"\n### Repo: {url}")
                    lines.append(summary or "(no analysis available)")

        lines.append("")
        lines.append("## Global implementation steps")
        if repo_info:
            lines.append(f"1. Repository context: {repo_info}")

        agent_task = format_agent_task(issue, input_prompt)
        requirements = self._extract_requirements(agent_task)
        start = 2 if repo_info else 1
        for i, req in enumerate(requirements, start):
            lines.append(f"{i}. {req}")

        lines.append("---")
        lines.append(f"## Task: {issue}")
        if requirements:
            lines.append("## Requirements:")
            for req in requirements:
                lines.append(f"- {req}")

        return "\n".join(lines)

    def _extract_requirements(self, issue: str) -> list[str]:
        """Extract actionable requirements from issue description."""
        requirements = []

        # Look for section markers
        sections = re.split(r"\n(?=## )", issue)
        for section in sections:
            if "###" in section:
                subsections = section.split("\n")
                for sub in subsections:
                    if sub.strip().startswith("###") and not sub.strip().startswith("####"):
                        req = sub.replace("### ", "").strip()
                        if req and len(req) > 10:
                            requirements.append(req)
            elif re.search(r"(?:-|\*)\s+\[select\]\s+.*?\[.*\]", section):
                matches = re.findall(r"(?:-|\*)\s+\[.*?\]\s+(.+)", section)
                requirements.extend(m.strip() for m in matches if m.strip())

        # If no structured requirements found, extract key file paths
        if not requirements:
            file_patterns = re.findall(
                r"`([^`]+/[^`]+Dockerfile|[^`]+/[^`]+yml|[^`]+\.ignore|[^`]+\.md)`", issue
            )
            for fp in file_patterns:
                requirements.append(f"Create or modify {fp}")

        if not requirements:
            requirements.append(f"Review codebase and implement the requested feature: {issue}")

        return requirements
