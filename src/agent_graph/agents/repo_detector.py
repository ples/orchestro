"""Repo detector: extracts target repositories from issue text."""

import os
import re
from dataclasses import dataclass

from agent_graph.state import RepoRecord, TaskState

from .base import BaseAgent


def _url_re(text: str) -> list[str]:
    return re.findall(
        r"https?://(?:git@)?(?:github\.com|bitbucket\.org|developers\.github\.com)"
        r"/"
        r"([^/]+/[^/\s]+?)(?:\.git)?\b",
        text,
    )


def _ref_re(text: str) -> list[str]:
    patterns = [
        r"(?:repo|repository|service|target)\b[:\s]+[`'\"]?([^`'\",;\s.]+(?:/[^`'\",;\s.]+)+)",
        r"(?:refs?\b|targets)\b[:\s]+[`'\"]?([^`'\",;\s.]+(?:/[^`'\",;\s.]+)+)",
        r"\*\*([^`'\",;\s.]+/[^`'\",;\s.]+/issues/\d+)\*\*",
    ]
    hits: list[str] = []
    for p in patterns:
        hits.extend(re.findall(p, text, re.IGNORECASE))
    return hits


def _org_re(text: str) -> list[str]:
    orgs: list[str] = []
    for var in ("GH_DEFAULT_OWNER", "BB_DEFAULT_OWNER"):
        val = os.getenv(var, "").strip()
        if val:
            orgs.append(val.lower())
    seen = set()
    refs = _ref_re(text) + _url_re(text)
    mentions: list[str] = []
    for ref in refs:
        for o in orgs:
            parent = ref.rsplit("/", 1)[0]
            if parent and parent.lower().startswith(o) and o not in seen:
                mentions.append(f"{parent}/*")
                seen.add(o)
    return mentions


@dataclass
class _Entity:
    kind: str          # "org" | "repo" | "service"
    name: str
    text_hint: str


def _detect(text: str) -> list[_Entity]:
    entities: list[_Entity] = []
    seen: set[str] = set()
    refs = _ref_re(text)
    urls = _url_re(text)
    orgs = _org_re(text)

    for ref in refs + urls:
        key = ref.lower()
        if key in seen:
            continue
        seen.add(key)
        if "/" in ref:
            parent = ref.rsplit("/", 1)[0]
            hit = any(parent.lower().startswith(o) for o in orgs)
            kind = "org" if hit else "repo"
            # skip URL-only references
            if url := next((u for u in urls if ref in u), None):
                entities.append(_Entity(kind=kind, name=ref, text_hint=url))
            else:
                entities.append(_Entity(kind=kind, name=ref, text_hint=""))

    services = re.findall(r"(?i)(api|gateway|backend|frontend|auth-service|payment|inventory|notification|data-engine|websocket|identity)", text)
    services = list(dict.fromkeys(services))
    for s in services:
        key = s.lower()
        if key not in seen:
            seen.add(key)
            entities.append(_Entity(kind="service", name=s, text_hint=f"service: {s}"))

    return entities


def _org_to_repos(org: str, text: str) -> list[tuple[str, str]]:
    raw_orgs = []
    for var in ("GH_DEFAULT_OWNER", "BB_DEFAULT_OWNER"):
        v = os.getenv(var, "").strip()
        if v:
            raw_orgs.append(v.lower())

    if not any(org.lower().startswith(o) for o in raw_orgs):
        return []

    url = org if "://" in org else org
    is_github_ish = "github.com" in url or any(
        org.lower().startswith(o) for o in raw_orgs if "gh_" in o.lower()
    )
    is_bb_ish = "bitbucket.org" in url or any(
        org.lower().startswith(o) for o in raw_orgs if "bb_" in o.lower()
    )

    if is_github_ish:
        import requests
        token = os.getenv("GITHUB_TOKEN", "")
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        res = requests.get(
            f"https://api.github.com/orgs/{org}/repos",
            headers=headers, params={"per_page": 100}, timeout=15,
        )
        if res.status_code == 200:
            for r in res.json():
                slug = r.get("full_name", "").removeprefix(org + "/")
                match = any(
                    re.search(re.sub(r"\*", r".*", s), slug, re.IGNORECASE)
                    for s in _ref_re(text)
                )
                if match:
                    yield org, f"https://github.com/{org}/{slug}.git"

    if is_bb_ish:
        import requests
        token = os.getenv("BITBUCKET_TOKEN", "")
        if token:
            res = requests.get(
                f"https://api.bitbucket.org/2.0/repositories/{org}/",
                headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
                params={"pagelen": 100}, timeout=15,
            )
            if res.status_code == 200:
                for r in res.json().get("values", []):
                    slug = r.get("name", "").removeprefix(org + "/")
                    match = any(
                        re.search(re.sub(r"\*", r".*", s), slug, re.IGNORECASE)
                        for s in _ref_re(text)
                    )
                    if match:
                        yield org, f"https://bitbucket.org/{org}/{slug}.git"


class RepoDetectorAgent(BaseAgent):
    """Detects target repositories from issue text."""

    name = "repo_detector"

    def _execute(self, state: TaskState) -> dict:
        issue = state.get("issue", "")
        if not issue:
            return {"target_repos": state.get("target_repos", [])}

        entities = _detect(issue)
        platforms = [state.get("source_platform", "github"), os.getenv("SOURCE_PLATFORM", "github")]

        records: list[RepoRecord] = []
        seen_urls: set[str] = set()

        for ent in entities:
            if ent.kind == "repo":
                url = ent.text_hint or f"https://github.com/{ent.name}" if "github.com" not in ent.name else ent.text_hint
                url = next((
                    u for u in [url or f"https://github.com/{ent.name}", ent.text_hint]
                    if u and ("github.com" in u or "bitbucket.org" in u)
                ), "")
                _contains_platform = any(
                    p.lower() in url.lower().split(".") for p in platforms
                )
                _no_github_in_url = "github.com" not in url.lower()
                if url and url not in seen_urls and (_contains_platform or _no_github_in_url):
                    records.append({"target_repo_path": url})
                    seen_urls.add(url)
                    print(f"  [Repo Detector] Detected repo: {url}")

            elif ent.kind == "org":
                resolved_repos = list(_org_to_repos(ent.name, issue))
                for _owner, repo_url in resolved_repos:
                    if repo_url and repo_url not in seen_urls:
                        records.append({"target_repo_path": repo_url})
                        seen_urls.add(repo_url)
                        print(f"  [Repo Detector] Detected repo (org lookup): {repo_url}")

            elif ent.kind == "service":
                for prefix in _ref_re(issue) + _url_re(issue):
                    if f"/{ent.name.lower()}" in prefix.lower():
                        url = next((u for u in _url_re(issue) if prefix in u), prefix)
                        if url and url not in seen_urls:
                            # try to turn owner/repo into full URL
                            if "github.com" not in url and "bitbucket.org" not in url:
                                url = f"https://github.com/{url}"
                            records.append({"target_repo_path": url})
                            seen_urls.add(url)
                            print(f"  [Repo Detector] Detected repo (service match): {url}")
                            break

        if not records:
            records = self._ask_for_repos(issue, seen_urls)

        if records:
            return {"target_repos": records}
        return {"target_repos": state.get("target_repos", [])}

    @staticmethod
    def _ask_for_repos(issue_text: str, seen_urls: set[str]) -> list[RepoRecord]:
        import sys as _sys
        try:
            _can_read = _sys.stdin.isatty()
            _can_read = _can_read and _sys.stdin.fileno() >= 0
            _can_read = _can_read and hasattr(_sys.stdin, "readline")
        except Exception:
            _can_read = False

        if not _can_read:
            return []

        try:
            _sys.stderr.write(
                "  [Repo Detector] No repos detected. Provide URLs or skip:\n"
            )
            _sys.stderr.flush()
            raw = input("  [Repo Detector] Target repos: ").strip()
        except (EOFError, OSError, ValueError, KeyboardInterrupt):
            return []

        if not raw or raw.strip().lower() == "skip":
            return []

        return [{"target_repo_path": url} for url in raw.split(",") if url.strip()]
