from __future__ import annotations

from pathlib import Path
from typing import Mapping


def discover_package_files(
    package_dirs: Mapping[str, tuple[Path, ...]],
    extension: str,
) -> dict[str, tuple[Path, ...]]:
    if extension not in {".rpm", ".deb"}:
        raise ValueError("extension must be .rpm or .deb")

    packages_by_repo: dict[str, tuple[Path, ...]] = {}
    for repo, dirs in package_dirs.items():
        repo_packages: list[Path] = []
        for directory in dirs:
            if not directory.exists():
                raise FileNotFoundError(f"{directory} does not exist")
            if not directory.is_dir():
                raise NotADirectoryError(f"{directory} is not a directory")
            repo_packages.extend(
                path
                for path in directory.rglob(f"*{extension}")
                if path.is_file() and _is_installable_package(path, extension)
            )
        packages_by_repo[repo] = tuple(sorted(repo_packages, key=lambda path: (path.name, str(path))))
    return packages_by_repo


def _is_installable_package(path: Path, extension: str) -> bool:
    if extension == ".rpm" and path.name.endswith(".src.rpm"):
        return False
    return True
