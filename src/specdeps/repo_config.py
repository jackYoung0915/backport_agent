from __future__ import annotations

import json
import re
from pathlib import Path

from .models import RepoRef


TREE_URL_RE = re.compile(
    r"^(?P<base>https://gitcode\.com/[^/]+/(?P<repo>[^/?#]+))/tree/(?P<branch>[^/?#]+)$"
)


def normalize_repo_url(page_url: str) -> RepoRef:
    match = TREE_URL_RE.match(page_url.strip())
    if not match:
        raise ValueError(f"unsupported GitCode tree URL: {page_url}")

    base = match.group("base")
    repo_name = match.group("repo")
    branch = match.group("branch")
    return RepoRef(
        name=repo_name,
        page_url=page_url.strip(),
        clone_url=f"{base}.git",
        branch=branch,
    )


def load_repos(config_path: str | Path) -> list[RepoRef]:
    path = Path(config_path)
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError(f"{path} must contain a JSON array")

    repos: list[RepoRef] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not entry.get("url"):
            raise ValueError(f"entry {index} must contain a url")
        repos.append(normalize_repo_url(str(entry["url"])))
    return repos
