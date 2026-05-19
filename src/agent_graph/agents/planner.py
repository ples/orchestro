import os
import re
import subprocess
import tempfile
from pathlib import Path

from agent_graph.openhands_client import OpenHandsClient
from agent_graph.state import TaskState

from .base import BaseAgent

# Source file extensions supported by the dependency analyser
SOURCE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".rb", ".php"}

# Common entry-point file / module names
ENTRY_POINT_NAMES = {"main", "app", "index", "__main__"}


class PlannerAgent(BaseAgent):
    """Plans implementation for a given issue."""

    name = "planner"

    def _execute(self, state: TaskState) -> dict:
        issue = state["issue"]
        print("\n[Planner]")
        print(f"Analyzing issue: {issue}")

        github_url = state.get("github_issue_url", "")
        repo_info = ""

        if github_url:
            repo_info = self._parse_repo_url(github_url)

        repo_context = self._fetch_repo_context(state)

        plan = self._build_plan(issue, repo_info, repo_context)

        return {"plan": plan, "repo_context": repo_context}

    def _fetch_repo_context(self, state: TaskState) -> str:
        """Clone (if needed) and analyse the repository structure + dependencies."""
        target = state.get("target_repo_path", "") or ""

        if not target or not target.strip():
            print("  [Repo] No target repo provided — skipping")
            return "(No repository provided — planner working without repo context)"

        # Decide whether to clone or use local path
        is_remote = (
            target.startswith("http://")
            or target.startswith("https://")
            or target.startswith("git@")
        )

        if is_remote:
            temp_dir = tempfile.mkdtemp(prefix="planner_repo_")
            repo_name = target.rstrip("/").split("/")[-1].replace(".git", "")
            clone_path = os.path.join(temp_dir, repo_name)
            print(f"  [Repo] Cloning {repo_name} to {temp_dir}")
            try:
                clone_url = OpenHandsClient._authenticated_git_url(target)
                subprocess.run(
                    ["git", "clone", "--depth", "1", clone_url, clone_path],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
                print(f"  [Repo] Cloned into {clone_path}")
                return self._analyse_repo(clone_path)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                msg = f"Failed to clone repo: {exc}"
                print(f"  [Repo] {msg}")
                return f"(Failed to clone repo: {exc})"
        else:
            p = Path(target)
            if not p.is_dir():
                msg = f"Target repo does not exist: {target}"
                print(f"  [Repo] {msg}")
                return f"(Target repo does not exist: {target})"
            print(f"  [Repo] Using local repo at {target}")
            return self._analyse_repo(str(p))

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

        # 5. Format output
        lines = []
        lines.append("=== Repository Context ===")
        lines.append("")
        lines.append("File tree (source files):")
        tree_files = sorted(dep_map.keys())
        for tf in tree_files:
            parts = Path(tf).parts
            indent = "  " * (len(parts) - 1)
            lines.append(f"{indent}{parts[-1]}")

        if entry_points:
            lines.append("\nEntry points (no internal dependencies):")
            for ep in entry_points:
                rel_stem = str(Path(ep).with_suffix(""))
                lines.append(f"  - {rel_stem}")

        if depth_map:
            mx_d = max(depth_map.values())
            for d in range(mx_d + 1):
                mods = sorted(f for f, dep in depth_map.items() if dep == d)
                if mods:
                    lines.append(f"\nLayer {d} (depth {d}):")
                    for m in mods:
                        imp_str = ", ".join(dep_map[m]) if dep_map[m] else "(standalone)"
                        lines.append(f"  - {m} => [{imp_str}]")

        lines.append("")
        lines.append("=== End Repository Context ===")
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

    def _build_plan(self, issue: str, repo_info: str, repo_context: str) -> str:
        """Generate implementation plan with repo context."""
        lines = []
        lines.append("=== PLAN ===")
        lines.append("")
        lines.append(repo_context)
        lines.append("")
        lines.append("Implementation steps:")

        if repo_info:
            lines.append(f"1. Repository context: {repo_info}")

        requirements = self._extract_requirements(issue)

        for i, req in enumerate(requirements, 1):
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
