from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models import PackagePathConfig


def load_package_config(
    config_path: str | Path | None,
    package_dir_overrides: tuple[str, ...],
    manager_override: str | None,
    sudo_override: bool | None,
) -> PackagePathConfig:
    payload: dict[str, Any] = {}
    if config_path:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"{config_path} must contain a JSON object")

    manager = str(manager_override or payload.get("manager") or "dnf")
    if manager not in {"dnf", "rpm", "apt", "dpkg"}:
        raise ValueError("manager must be dnf, rpm, apt, or dpkg")

    sudo = _parse_sudo(payload.get("sudo", True), sudo_override)
    package_dirs = _parse_package_dirs(payload.get("package_dirs", {}))
    package_names = _parse_package_names(payload.get("package_names", {}))

    for override in package_dir_overrides:
        if "=" not in override:
            raise ValueError("--package-dir must use repo=path")
        repo, raw_path = override.split("=", 1)
        if not repo or not raw_path:
            raise ValueError("--package-dir must use repo=path")
        package_dirs[repo] = (Path(raw_path),)

    return PackagePathConfig(package_dirs=package_dirs, package_names=package_names, manager=manager, sudo=sudo)


def _parse_sudo(config_value: Any, sudo_override: bool | None) -> bool:
    if sudo_override is not None:
        return sudo_override
    if not isinstance(config_value, bool):
        raise ValueError("sudo must be a boolean")
    return config_value


def _parse_package_dirs(value: Any) -> dict[str, tuple[Path, ...]]:
    if value in ({}, None):
        return {}
    if not isinstance(value, dict):
        raise ValueError("package_dirs must be an object")

    result: dict[str, tuple[Path, ...]] = {}
    for repo, paths in value.items():
        if isinstance(paths, str):
            result[str(repo)] = (Path(paths),)
        elif isinstance(paths, list) and all(isinstance(item, str) for item in paths):
            result[str(repo)] = tuple(Path(item) for item in paths)
        else:
            raise ValueError(f"package_dirs for {repo} must be a string or list of strings")
    return result


def _parse_package_names(value: Any) -> dict[str, tuple[str, ...]]:
    if value in ({}, None):
        return {}
    if not isinstance(value, dict):
        raise ValueError("package_names must be an object")

    result: dict[str, tuple[str, ...]] = {}
    for repo, names in value.items():
        if isinstance(names, str):
            result[str(repo)] = (names,)
        elif isinstance(names, list) and all(isinstance(item, str) for item in names):
            result[str(repo)] = tuple(names)
        else:
            raise ValueError(f"package_names for {repo} must be a string or list of strings")
    return result
