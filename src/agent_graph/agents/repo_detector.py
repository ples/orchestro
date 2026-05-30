"""Repo detector: extracts target repositories from issue text."""

import os
import re
from dataclasses import dataclass

from agent_graph.state import RepoRecord, TaskState

from .base import BaseAgent

# Default service-to-repo name mapping.
# Extend this with env var REPO_SERVICE_MAP: comma-separated "keyword=repo_name" pairs.
_COMPONENT_SERVICE_MAP: dict[str, str] = {
    "minsky identity hub": "identity-hub",
}

_DEFAULT_SERVICE_MAP: dict[str, str] = {
    "admin": "admin-ui",
    "admin ui": "admin-ui",
    "admin app": "admin-ui",
    "identity-hub": "identity-hub",
    "identity hub": "identity-hub",
    "identity": "identity-hub",
    "account settings": "account-settings",
    "accounts": "account-settings",
    "gateway": "gateway",
    "auth": "auth-service",
    "payment": "payment",
    "notification": "notification",
    "data": "data-engine",
}

# Load additional service->repo mappings from env var
def _load_service_map() -> dict[str, str]:
    """Combine default map with REPO_SERVICE_MAP env var."""
    result = dict(_DEFAULT_SERVICE_MAP)
    raw = os.getenv("REPO_SERVICE_MAP", "").strip()
    if not raw:
        return result
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            key, val = pair.split("=", 1)
            result[key.strip().lower()] = val.strip()
    return result


def _ambiguous_tokens_blocked(service_map: dict[str, str]) -> frozenset[str]:
    """Ambiguous tokens to skip unless explicitly set in REPO_SERVICE_MAP."""
    allowed: set[str] = set()
    raw = os.getenv("REPO_SERVICE_MAP", "").strip()
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            key = pair.split("=", 1)[0].strip().lower()
            if key in _AMBIGUOUS_SERVICE_TOKENS:
                allowed.add(key)
    return frozenset(t for t in _AMBIGUOUS_SERVICE_TOKENS if t not in allowed)


def _load_explicit_repo_urls() -> dict[str, str]:
    """Optional slug=url overrides via JIRA_REPO_MAP (comma-separated pairs)."""
    result: dict[str, str] = {}
    raw = os.getenv("JIRA_REPO_MAP", "").strip()
    if not raw:
        return result
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            slug, url = pair.split("=", 1)
            result[slug.strip().lower()] = url.strip()
    return result


def _prefer_bitbucket(platform: str) -> bool:
    return platform in ("jira", "bitbucket")


def service_slug_to_repo_url(slug: str, source_platform: str) -> str | None:
    """Map a service/repo slug to a clone URL for the given platform."""
    slug_key = slug.lower().strip()
    explicit = _load_explicit_repo_urls().get(slug_key)
    if explicit:
        return explicit

    bb_owner = os.getenv("BB_DEFAULT_OWNER", "").strip()
    gh_owner = os.getenv("GH_DEFAULT_OWNER", "").strip()

    if _prefer_bitbucket(source_platform):
        from agent_graph.agents.bitbucket_catalog import resolve_bitbucket_repo_url

        api_url = resolve_bitbucket_repo_url(slug_key, bb_owner)
        if api_url:
            return api_url
        if bb_owner:
            return f"https://bitbucket.org/{bb_owner}/{slug_key}.git"
        return None

    if gh_owner:
        return f"https://github.com/{gh_owner}/{slug_key}.git"
    return None


# Bare tokens that mean "layer" or "HTTP API" in prose — never match via \bword\b alone.
_AMBIGUOUS_SERVICE_TOKENS = frozenset({"api", "backend", "frontend"})

# Explicit multi-word / slug patterns (checked before generic keyword fallback).
_COMPOUND_SERVICE_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)\bidentity\s+hub\s+api\b", "identity-hub"),
    (r"(?i)\bidentity-hub-api\b", "identity-hub"),
    (r"(?i)\badmin-ui-api\b", "admin-ui"),
    (r"(?i)\badmin\s+ui\s+api\b", "admin-ui"),
    (r"(?i)\baccount-ui-api\b", "account-settings"),
    (r"(?i)\bbackend\s+(?:service|repo|repository)\b", "backend"),
    (r"(?i)\bapi\s+(?:service|repo|repository)\b", "api"),
]

# Common service keywords used for pattern-matching in issue text
_SERVICE_KEYWORDS = [
    "admin",
    "identity-hub",
    "identity hub",
    "gateway",
    "auth",
    "auth-service",
    "payment",
    "notification",
    "data-engine",
    "websocket",
    "account settings",
    "admin-ui",
    "account-settings",
]


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


def _detect_compound_services(text: str) -> list[tuple[str, str]]:
    """Return (raw_match, repo_slug) for explicit compound patterns only."""
    hits: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pattern, slug in _COMPOUND_SERVICE_PATTERNS:
        for m in re.finditer(pattern, text):
            key = slug.lower()
            if key not in seen:
                seen.add(key)
                hits.append((m.group(0), slug))
    return hits


def _detect_components(text: str) -> list[_Entity]:
    entities: list[_Entity] = []
    service_map = _load_service_map()
    combined = {**_COMPONENT_SERVICE_MAP, **service_map}
    for match in re.finditer(r"(?i)components?\s*:\s*([^\n]+)", text):
        for name in match.group(1).split(","):
            comp = name.strip().lower()
            slug = combined.get(comp)
            if slug:
                entities.append(
                    _Entity(kind="service", name=slug, text_hint=f"component: {name.strip()}")
                )
    return entities


def _detect(text: str) -> list[_Entity]:
    entities: list[_Entity] = []
    seen: set[str] = set()
    entities.extend(_detect_components(text))
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

    service_map = _load_service_map()
    blocked_ambiguous = _ambiguous_tokens_blocked(service_map)

    found_services: list[tuple[str, str]] = []  # (raw_match, repo_name)

    for raw_match, repo_name in _detect_compound_services(text):
        if (raw_match, repo_name) not in found_services:
            found_services.append((raw_match, repo_name))

    # Match longer phrases first (e.g. "admin app" before "admin").
    service_phrases = sorted(service_map.keys(), key=len, reverse=True)
    for phrase in service_phrases:
        if phrase.lower() in blocked_ambiguous:
            continue
        pattern = re.compile(
            r"(?i)(?:^|[\s\-_/,;:.|])(" + re.escape(phrase) + r")(?:\s|$|[\s\-_/,;:.|])"
        )
        for m in pattern.finditer(text):
            repo_name = service_map[phrase]
            raw_match = m.group(1)
            if (raw_match, repo_name) not in found_services:
                found_services.append((raw_match, repo_name))

    # Fallback: individual keywords (skip ambiguous bare tokens).
    service_keywords_lower = sorted(
        {kw for kw in _SERVICE_KEYWORDS if kw.lower() not in blocked_ambiguous},
        key=len,
        reverse=True,
    )
    for kw in service_keywords_lower:
        if kw.lower() in blocked_ambiguous:
            continue
        pattern = re.compile(r"(?i)\b(" + re.escape(kw) + r")\b")
        m = pattern.search(text)
        if m:
            repo_name = service_map.get(kw) or kw
            if repo_name.lower() in blocked_ambiguous:
                continue
            raw_match = m.group(1)
            if (raw_match, repo_name) not in found_services:
                found_services.append((raw_match, repo_name))

    for raw_match, repo_name in found_services:
        key = repo_name.lower()
        if key not in seen:
            seen.add(key)
            entities.append(
                _Entity(kind="service", name=repo_name, text_hint=f"service: {raw_match}")
            )

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
        from agent_graph.agents.bitbucket_catalog import BitbucketRepoCatalog

        workspace = org.split("/")[-1] if "/" in org else org
        catalog = BitbucketRepoCatalog(workspace=workspace)
        if not catalog.available():
            return
        try:
            for repo in catalog.list_repos():
                slug = repo.slug
                match = any(
                    re.search(re.sub(r"\*", r".*", s), slug, re.IGNORECASE)
                    for s in _ref_re(text)
                )
                if match:
                    yield workspace, repo.clone_url
        except RuntimeError as exc:
            print(f"  [Repo Detector] Bitbucket list failed: {exc}")


class RepoDetectorAgent(BaseAgent):
    """Detects target repositories from issue text."""

    name = "repo_detector"

    def _execute(self, state: TaskState) -> dict:
        issue = state.get("issue", "")
        if not issue:
            return {"target_repos": state.get("target_repos", [])}

        from agent_graph.state import format_agent_task

        detect_text = format_agent_task(issue, state.get("input_prompt", ""))
        entities = _detect(detect_text)
        platforms = [state.get("source_platform", "github"), os.getenv("SOURCE_PLATFORM", "github")]

        records: list[RepoRecord] = []
        seen_urls: set[str] = set()
        unresolved_services: list[tuple[str, str]] = []

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
                service_name = ent.name.lower()
                resolved = False
                for prefix in _ref_re(issue) + _url_re(issue):
                    if service_name in prefix.lower():
                        url = next((u for u in _url_re(issue) if prefix in u), prefix)
                        if url and url not in seen_urls:
                            if "github.com" not in url and "bitbucket.org" not in url:
                                host = (
                                    "bitbucket.org"
                                    if _prefer_bitbucket(platforms[0])
                                    else "github.com"
                                )
                                url = f"https://{host}/{url.lstrip('/')}"
                            records.append({"target_repo_path": url})
                            seen_urls.add(url)
                            print(f"  [Repo Detector] Detected repo (service match): {url}")
                            resolved = True
                            break
                if not resolved:
                    platform = platforms[0]
                    url = service_slug_to_repo_url(service_name, platform)
                    if url and url not in seen_urls:
                        records.append({"target_repo_path": url})
                        seen_urls.add(url)
                        print(
                            f"  [Repo Detector] Resolved service '{service_name}' -> {url}"
                        )
                    else:
                        unresolved_services.append((service_name, platform))

        if not records:
            records = self._detect_via_llm(
                detect_text=detect_text,
                input_prompt=state.get("input_prompt", ""),
                source_platform=platforms[0],
                seen_urls=seen_urls,
            )

        if not records:
            print("  [Repo Detector] No target repositories could be successfully detected or resolved.")
            if unresolved_services:
                print("  [Repo Detector] Details: Attempted to resolve the following services from text keywords but failed:")
                for service_name, platform in unresolved_services:
                    print(f"    - Service name: '{service_name}' (Target Platform: {platform})")
                    if platform == "github":
                        gh_owner = os.getenv("GH_DEFAULT_OWNER", "").strip()
                        if not gh_owner:
                            print(
                                "      Cause: source_platform is 'github' but GH_DEFAULT_OWNER is not set in the environment (.env).\n"
                                "             Please configure GH_DEFAULT_OWNER=your_github_org_or_user in your environment."
                            )
                        else:
                            print(
                                f"      Cause: Checked repository 'https://github.com/{gh_owner}/{service_name}.git' "
                                f"but it did not exist, or you lack read permissions."
                            )
                    elif platform in ("jira", "bitbucket"):
                        bb_owner = os.getenv("BB_DEFAULT_OWNER", "").strip()
                        bb_token = os.getenv("BITBUCKET_TOKEN", "").strip()
                        if not bb_owner:
                            print(
                                "      Cause: Target platform is 'bitbucket'/'jira' but BB_DEFAULT_OWNER is not set in the environment (.env).\n"
                                "             Please configure BB_DEFAULT_OWNER=your_bitbucket_workspace in your environment."
                            )
                        elif not bb_token:
                            print(
                                "      Cause: BITBUCKET_TOKEN is not set in the environment (.env).\n"
                                "             Please configure BITBUCKET_TOKEN=your_app_password_or_token in your environment."
                            )
                        else:
                            print(
                                f"      Cause: Could not find a repository matching slug '{service_name}' in Bitbucket "
                                f"workspace '{bb_owner}'. Checked Bitbucket Catalog API and found no match."
                            )
            else:
                print("  [Repo Detector] Cause: No repository URLs, organizations, or service keywords (e.g. 'admin ui', 'auth') were found in the issue text.")
            
            records = self._ask_for_repos(state, seen_urls)

        if records:
            return {"target_repos": records}
        return {"target_repos": state.get("target_repos", [])}

    @staticmethod
    def _detect_via_llm(
        *,
        detect_text: str,
        input_prompt: str,
        source_platform: str,
        seen_urls: set[str],
    ) -> list[RepoRecord]:
        from agent_graph.repo_llm_extractor import (
            catalog_entries_to_records,
            extract_target_repo_slugs,
            list_workspace_catalog,
            llm_repo_detect_enabled,
        )

        if not llm_repo_detect_enabled():
            return []

        catalog = list_workspace_catalog(source_platform)
        if not catalog:
            print(
                "  [Repo Detector] LLM repo detection skipped: "
                "workspace catalog unavailable (BB_DEFAULT_OWNER+BITBUCKET_TOKEN or GH_DEFAULT_OWNER)"
            )
            return []

        print(
            f"  [Repo Detector] LLM selecting repos from catalog ({len(catalog)} repos)..."
        )
        slugs, reason = extract_target_repo_slugs(
            detect_text,
            input_prompt,
            catalog,
        )
        if not slugs:
            if reason:
                print(f"  [Repo Detector] LLM found no matching repos: {reason}")
            return []

        records = catalog_entries_to_records(slugs, catalog)
        out: list[RepoRecord] = []
        for record in records:
            url = record.get("target_repo_path", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                out.append(record)
                print(f"  [Repo Detector] LLM selected repo: {url}")
        return out

    @staticmethod
    def _ask_for_repos(state: TaskState, seen_urls: set[str]) -> list[RepoRecord]:
        import sys as _sys

        # Bypass interactive stdin prompts when running inside Telegram bot mode
        if state.get("chat_id"):
            return []

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
