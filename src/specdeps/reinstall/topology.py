from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import TopologyData


def load_topology(path: str | Path) -> TopologyData:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")

    repos = tuple(str(repo) for repo in _require_list(payload, "repos"))
    edges_payload = _require_list(payload, "edges")
    specs_payload = payload.get("specs")
    if not isinstance(specs_payload, list):
        raise ValueError(f"{path} missing specs")

    edges: list[tuple[str, str, str]] = []
    for index, edge in enumerate(edges_payload):
        if not isinstance(edge, dict):
            raise ValueError(f"edge {index} must be an object")
        edges.append((str(edge["source_repo"]), str(edge["target_repo"]), str(edge["dependency"])))

    packages_by_repo: dict[str, list[str]] = {repo: [] for repo in repos}
    seen_by_repo: dict[str, set[str]] = {repo: set() for repo in repos}
    for index, spec in enumerate(specs_payload):
        if not isinstance(spec, dict):
            raise ValueError(f"spec {index} must be an object")
        repo = str(spec.get("repo", ""))
        packages = spec.get("packages")
        if repo not in packages_by_repo:
            continue
        if not isinstance(packages, list):
            raise ValueError(f"spec {index} packages must be a list")
        for package in packages:
            package_name = str(package)
            if package_name not in seen_by_repo[repo]:
                packages_by_repo[repo].append(package_name)
                seen_by_repo[repo].add(package_name)

    missing = [repo for repo, packages in packages_by_repo.items() if not packages]
    if missing:
        raise ValueError(f"{path} missing package data for: {', '.join(missing)}")

    return TopologyData(
        repos=repos,
        edges=tuple(edges),
        packages_by_repo={repo: tuple(packages) for repo, packages in packages_by_repo.items()},
    )


def _require_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"topology {key} must be a list")
    return value
