from __future__ import annotations

import subprocess
from pathlib import Path

from .models import RepoPaths, RepoRef


def checkout_repos(repos: list[RepoRef], checkout_dir: str | Path) -> RepoPaths:
    root = Path(checkout_dir)
    root.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {}
    for repo in repos:
        target = root / repo.name
        if (target / ".git").exists():
            subprocess.run(["git", "-C", str(target), "fetch", "--depth", "1", "origin", repo.branch], check=True)
            subprocess.run(["git", "-C", str(target), "checkout", "FETCH_HEAD"], check=True)
        else:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", repo.branch, repo.clone_url, str(target)],
                check=True,
            )
        paths[repo.name] = target

    return paths


def find_spec_files(repo_path: str | Path) -> list[Path]:
    root = Path(repo_path)
    root_specs = sorted(root.glob("*.spec"))
    if root_specs:
        return root_specs
    return sorted(root.rglob("*.spec"))
