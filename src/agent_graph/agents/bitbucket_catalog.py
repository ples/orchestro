"""Discover Bitbucket repositories via the workspace API."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache

import requests

BITBUCKET_API = "https://api.bitbucket.org/2.0"


@dataclass(frozen=True)
class BitbucketRepo:
    workspace: str
    slug: str
    full_name: str

    @property
    def clone_url(self) -> str:
        return f"https://bitbucket.org/{self.workspace}/{self.slug}.git"


def _bitbucket_username() -> str:
    return (
        os.getenv("BB_USERNAME", "").strip()
        or os.getenv("ATLASSIAN_EMAIL", "").strip()
        or os.getenv("JIRA_EMAIL", "").strip()
    )


def _bitbucket_token() -> str:
    return os.getenv("BITBUCKET_TOKEN", "").strip()


def _auth() -> tuple[str, str] | None:
    user = _bitbucket_username()
    token = _bitbucket_token()
    if user and token:
        return (user, token)
    return None


def _workspace() -> str:
    return os.getenv("BB_DEFAULT_OWNER", "").strip()


def _score_slug_match(want: str, repo_slug: str) -> int:
    want_norm = want.lower().replace("_", "-").strip()
    slug_norm = repo_slug.lower().replace("_", "-").strip()
    if not want_norm or not slug_norm:
        return 0
    if want_norm == slug_norm:
        return 1000
    if want_norm in slug_norm:
        return 700 + len(want_norm)
    if slug_norm in want_norm:
        return 600 + len(slug_norm)
    want_tokens = set(want_norm.split("-"))
    slug_tokens = set(slug_norm.split("-"))
    overlap = want_tokens & slug_tokens
    if overlap:
        return 200 + 50 * len(overlap)
    return 0


class BitbucketRepoCatalog:
    """Lists and resolves repos in a Bitbucket workspace using the REST API."""

    def __init__(
        self,
        workspace: str | None = None,
        auth: tuple[str, str] | None = None,
        *,
        page_size: int = 100,
        max_pages: int = 10,
    ) -> None:
        self.workspace = (workspace or _workspace()).strip()
        self.auth = auth if auth is not None else _auth()
        self.page_size = page_size
        self.max_pages = max_pages
        self._repos: list[BitbucketRepo] | None = None

    def available(self) -> bool:
        return bool(self.workspace and self.auth)

    def _get(self, path: str, *, params: dict | None = None) -> dict:
        if not self.auth:
            msg = "BITBUCKET_TOKEN and JIRA_EMAIL (or BB_USERNAME) are required"
            raise RuntimeError(msg)
        url = path if path.startswith("http") else f"{BITBUCKET_API}{path}"
        resp = requests.get(
            url,
            params=params or {},
            auth=self.auth,
            headers={"Accept": "application/json"},
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Bitbucket API error {resp.status_code} for {path}: {resp.text[:500]}"
            )
        data = resp.json()
        if not isinstance(data, dict):
            msg = f"Unexpected Bitbucket API response for {path}"
            raise RuntimeError(msg)
        return data

    def list_repos(self, *, query: str = "", refresh: bool = False) -> list[BitbucketRepo]:
        if query:
            return self._fetch_page(query=query)

        if self._repos is not None and not refresh:
            return self._repos

        repos: list[BitbucketRepo] = []
        page = 1
        while page <= self.max_pages:
            batch = self._fetch_page(page=page, query="")
            if not batch:
                break
            repos.extend(batch)
            if len(batch) < self.page_size:
                break
            page += 1

        self._repos = repos
        return repos

    def _fetch_page(self, *, page: int = 1, query: str = "") -> list[BitbucketRepo]:
        params: dict[str, str | int] = {
            "page": page,
            "pagelen": self.page_size,
            "sort": "-updated_on",
            "fields": "values.full_name,values.slug,next",
        }
        if query:
            params["q"] = query
        else:
            params["q"] = ""

        data = self._get(f"/repositories/{self.workspace}", params=params)
        repos: list[BitbucketRepo] = []
        for item in data.get("values", []):
            if not isinstance(item, dict):
                continue
            slug = str(item.get("slug", "")).strip()
            full_name = str(item.get("full_name", "")).strip()
            if not slug:
                continue
            if not full_name:
                full_name = f"{self.workspace}/{slug}"
            repos.append(
                BitbucketRepo(workspace=self.workspace, slug=slug, full_name=full_name)
            )
        return repos

    def search_repos(self, name_hint: str) -> list[BitbucketRepo]:
        hint = name_hint.strip().replace("_", "-")
        if not hint:
            return []
        safe = re.sub(r'["\\]', "", hint)
        return self._fetch_page(query=f'name~"{safe}"')

    def resolve_slug(self, slug: str) -> BitbucketRepo | None:
        slug = slug.strip().lower().replace("_", "-")
        if not slug or not self.available():
            return None

        best: BitbucketRepo | None = None
        best_score = 0

        for repo in self.list_repos():
            score = _score_slug_match(slug, repo.slug)
            if score > best_score:
                best_score = score
                best = repo

        if best_score >= 200:
            return best

        for repo in self.search_repos(slug):
            score = _score_slug_match(slug, repo.slug)
            if score > best_score:
                best_score = score
                best = repo

        if best_score >= 200:
            return best

        return None


@lru_cache(maxsize=8)
def get_catalog(workspace: str) -> BitbucketRepoCatalog:
    return BitbucketRepoCatalog(workspace=workspace)


def resolve_bitbucket_repo_url(slug: str, workspace: str | None = None) -> str | None:
    """Resolve a service slug to a clone URL via the Bitbucket API."""
    ws = (workspace or _workspace()).strip()
    if not ws:
        return None
    catalog = get_catalog(ws)
    if not catalog.available():
        return None
    try:
        repo = catalog.resolve_slug(slug)
    except RuntimeError as exc:
        print(f"  [Bitbucket Catalog] API lookup failed: {exc}")
        return None
    if repo:
        print(
            f"  [Bitbucket Catalog] Resolved '{slug}' -> {repo.slug} ({repo.clone_url})"
        )
        return repo.clone_url
    return None
