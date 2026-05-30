"""LLM-based target repository selection from workspace catalog + task text."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

from agent_graph.agents.bitbucket_catalog import BitbucketRepoCatalog, _score_slug_match
from agent_graph.llm_client import chat_completion
from agent_graph.message_classifier import _extract_json_blob

logger = logging.getLogger(__name__)

_REPO_EXTRACTOR_SYSTEM = """You select which repositories must be modified to implement an engineering task.

You receive:
1. A catalog of repository slugs available in the workspace (one per line)
2. Issue text — ticket body or task description
3. Developer prompt — optional extra instructions (may be empty)

Return ONLY a JSON object:
{
  "repos": ["slug1"],
  "reason": "one short sentence"
}

Rules:
- "repos" must contain exact slugs copied from the catalog list (case-sensitive)
- Pick the minimal set needed; include multiple only when the task clearly spans them
- Map informal names to catalog slugs (e.g. "jenkins pipeline repo" -> jenkins-pipeline)
- Return empty repos if the task is not about code in any catalog repo
- Do not invent slugs not present in the catalog"""


@dataclass(frozen=True)
class CatalogEntry:
    slug: str
    full_name: str
    clone_url: str


def llm_repo_detect_enabled() -> bool:
    raw = os.getenv("REPO_LLM_DETECT", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def max_catalog_size() -> int:
    try:
        return max(20, int(os.getenv("REPO_LLM_MAX_CATALOG", "400")))
    except ValueError:
        return 400


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def list_bitbucket_catalog(workspace: str | None = None) -> list[CatalogEntry]:
    catalog = BitbucketRepoCatalog(workspace=workspace)
    if not catalog.available():
        return []
    try:
        repos = catalog.list_repos()
    except RuntimeError as exc:
        logger.warning("Bitbucket catalog list failed: %s", exc)
        return []
    return [
        CatalogEntry(slug=r.slug, full_name=r.full_name, clone_url=r.clone_url)
        for r in repos
    ]


def list_github_catalog(owner: str | None = None) -> list[CatalogEntry]:
    import requests

    gh_owner = (owner or os.getenv("GH_DEFAULT_OWNER", "")).strip()
    if not gh_owner:
        return []

    token = os.getenv("GITHUB_TOKEN", "")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    entries: list[CatalogEntry] = []
    cap = max_catalog_size()
    url: str | None = (
        f"https://api.github.com/orgs/{gh_owner}/repos?per_page=100&sort=updated"
    )
    try:
        while url and len(entries) < cap:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                logger.warning("GitHub repo list failed: %s", resp.status_code)
                break
            batch = resp.json()
            if not isinstance(batch, list) or not batch:
                break
            for repo in batch:
                if not isinstance(repo, dict):
                    continue
                slug = str(repo.get("name", "")).strip()
                full_name = str(repo.get("full_name", "")).strip() or f"{gh_owner}/{slug}"
                clone_url = str(repo.get("clone_url", "")).strip()
                if slug and clone_url:
                    entries.append(
                        CatalogEntry(
                            slug=slug, full_name=full_name, clone_url=clone_url
                        )
                    )
            link = resp.headers.get("Link", "")
            m = re.search(r'<([^>]+)>;\s*rel="next"', link)
            url = m.group(1) if m else None
    except Exception as exc:
        logger.warning("GitHub repo list failed: %s", exc)
    return entries[:cap]


def _prefer_bitbucket(platform: str) -> bool:
    return platform in ("jira", "bitbucket")


def list_workspace_catalog(source_platform: str) -> list[CatalogEntry]:
    if _prefer_bitbucket(source_platform):
        entries = list_bitbucket_catalog()
        if entries:
            return entries
    entries = list_github_catalog()
    if entries:
        return entries
    if not _prefer_bitbucket(source_platform):
        return list_bitbucket_catalog()
    return []


def _prefilter_catalog(
    issue_text: str,
    input_prompt: str,
    catalog: list[CatalogEntry],
    *,
    limit: int,
) -> list[CatalogEntry]:
    if len(catalog) <= limit:
        return catalog

    combined = f"{issue_text}\n{input_prompt}".lower()
    tokens = {
        t
        for t in re.split(r"[\s\-_/.]+", combined)
        if len(t) >= 3 and t not in ("the", "and", "for", "repo", "repository")
    }

    scored: list[tuple[int, CatalogEntry]] = []
    for entry in catalog:
        score = _score_slug_match(" ".join(tokens), entry.slug)
        for token in tokens:
            score = max(score, _score_slug_match(token, entry.slug))
            if token in entry.slug.lower() or token in entry.full_name.lower():
                score = max(score, 500)
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    picked = [entry for _, entry in scored[:limit]]
    if len(picked) < limit:
        seen = {e.slug for e in picked}
        for entry in catalog:
            if entry.slug not in seen:
                picked.append(entry)
            if len(picked) >= limit:
                break
    return picked[:limit]


def _build_catalog_prompt(catalog: list[CatalogEntry]) -> str:
    lines = [f"{e.slug}\t# {e.full_name}" for e in catalog]
    return "Available repository slugs (use exact slug values):\n" + "\n".join(lines)


def _parse_extractor_response(raw: str, valid_slugs: set[str]) -> tuple[list[str], str]:
    blob = _extract_json_blob(raw)
    if not blob:
        return [], ""
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        return [], ""

    reason = ""
    repos_raw: object
    if isinstance(data, list):
        repos_raw = data
    elif isinstance(data, dict):
        repos_raw = data.get("repos")
        reason = str(data.get("reason") or "")
    else:
        return [], ""

    if not isinstance(repos_raw, list):
        return [], reason

    slugs: list[str] = []
    seen: set[str] = set()
    slug_lookup = {s.lower(): s for s in valid_slugs}
    for item in repos_raw:
        if not isinstance(item, str):
            continue
        slug = item.strip()
        if not slug:
            continue
        canonical = slug_lookup.get(slug.lower())
        if canonical and canonical not in seen:
            seen.add(canonical)
            slugs.append(canonical)
    return slugs, str(data.get("reason") or "")


def extract_target_repo_slugs(
    issue_text: str,
    input_prompt: str,
    catalog: list[CatalogEntry],
) -> tuple[list[str], str]:
    """Return catalog slugs the LLM believes are needed for the task."""
    if not catalog:
        return [], ""

    limit = max_catalog_size()
    catalog_slice = _prefilter_catalog(issue_text, input_prompt, catalog, limit=limit)
    if len(catalog_slice) < len(catalog):
        logger.info(
            "Repo LLM catalog pre-filtered %d -> %d slugs",
            len(catalog),
            len(catalog_slice),
        )

    valid_slugs = {e.slug for e in catalog_slice}
    user_prompt = (
        f"{_build_catalog_prompt(catalog_slice)}\n\n"
        f"---\nIssue text:\n{_truncate(issue_text, 6000)}\n\n"
        f"---\nDeveloper prompt:\n{_truncate(input_prompt, 2000) or '(none)'}"
    )

    raw = chat_completion(
        _REPO_EXTRACTOR_SYSTEM,
        user_prompt,
        max_tokens=512,
        temperature=0.0,
        json_mode=True,
    )
    if not raw:
        return [], ""

    slugs, reason = _parse_extractor_response(raw, valid_slugs)
    if slugs:
        logger.info("Repo LLM selected: %s (%s)", slugs, reason)
    else:
        logger.info("Repo LLM selected no repos (%s)", reason or "unparsed")
    return slugs, reason


def catalog_entries_to_records(
    slugs: list[str],
    catalog: list[CatalogEntry],
) -> list[dict[str, str]]:
    by_slug = {e.slug: e for e in catalog}
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for slug in slugs:
        entry = by_slug.get(slug)
        if not entry or entry.clone_url in seen:
            continue
        records.append({"target_repo_path": entry.clone_url})
        seen.add(entry.clone_url)
    return records
