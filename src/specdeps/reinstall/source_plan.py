from __future__ import annotations

from typing import Any


def build_source_reinstall_json(
    selected_packages: tuple[str, ...],
    manager: str,
    *,
    sudo: bool = True,
) -> dict[str, Any]:
    if manager not in {"dnf", "apt"}:
        raise ValueError("manager must be dnf or apt")
    packages = _dedupe(selected_packages)
    if not packages:
        raise ValueError("upgrade_packages must be non-empty")

    actions: list[dict[str, Any]] = []
    for package_name in packages:
        actions.append(
            {
                "phase": "uninstall",
                "package": package_name,
                "command": _remove_command(manager, package_name, sudo),
            }
        )
    for package_name in packages:
        actions.append(
            {
                "phase": "install",
                "package": package_name,
                "command": _install_command(manager, package_name, sudo),
            }
        )

    return {
        "version": 1,
        "install_source": "configured_repo",
        "manager": manager,
        "selected_packages": list(packages),
        "order": {"uninstall": list(packages), "install": list(packages)},
        "actions": actions,
        "warnings": [],
    }


def _remove_command(manager: str, package_name: str, sudo: bool) -> list[str]:
    if manager == "dnf":
        return _base(sudo) + ["dnf", "remove", "-y", package_name]
    return _base(sudo) + ["apt-get", "remove", "-y", package_name]


def _install_command(manager: str, package_name: str, sudo: bool) -> list[str]:
    if manager == "dnf":
        return _base(sudo) + ["dnf", "install", "-y", package_name]
    return _base(sudo) + ["apt-get", "install", "-y", package_name]


def _base(sudo: bool) -> list[str]:
    return ["sudo"] if sudo else []


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)
