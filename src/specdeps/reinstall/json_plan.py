from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .package_metadata import PackageMetadata


@dataclass(frozen=True)
class ReinstallInput:
    upgrade_packages: tuple[str, ...]
    upgrade_paths: tuple[Path, ...]


def load_reinstall_input(path: str | Path) -> ReinstallInput:
    input_path = Path(path)
    if not input_path.exists():
        raise FileNotFoundError(f"{input_path} does not exist")
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{input_path} must contain a JSON object")
    packages = _require_string_list(payload, "upgrade_packages")
    paths = _require_string_list(payload, "upgrade_paths")
    return ReinstallInput(
        upgrade_packages=tuple(packages),
        upgrade_paths=tuple(Path(value) for value in paths),
    )


def discover_upgrade_artifacts(paths: tuple[Path, ...]) -> tuple[str, tuple[Path, ...]]:
    artifacts: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        candidates = _artifact_candidates(path)
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved not in seen:
                artifacts.append(candidate)
                seen.add(resolved)

    formats = {_artifact_format(path) for path in artifacts}
    formats.discard("")
    if not artifacts:
        raise ValueError("no RPM or DEB upgrade packages found")
    if len(formats) > 1:
        raise ValueError("cannot mix RPM and DEB upgrade packages")
    package_format = formats.pop()
    return package_format, tuple(sorted(artifacts, key=lambda path: (path.name, str(path))))


def build_reinstall_json(
    selected_packages: tuple[str, ...],
    packages: list[PackageMetadata],
    package_format: str,
    *,
    include_available_dependencies: bool = True,
) -> dict[str, Any]:
    if package_format not in {"rpm", "deb"}:
        raise ValueError("package format must be rpm or deb")
    if not selected_packages:
        raise ValueError("upgrade_packages must be non-empty")

    metadata_by_name = {package.name: package for package in packages}
    provider_index: dict[str, str] = {}
    for package in packages:
        provider_index.setdefault(package.name, package.name)
        for provided in package.provides:
            provider_index.setdefault(provided, package.name)

    missing = [name for name in selected_packages if name not in provider_index]
    if missing:
        raise ValueError(f"upgrade package not found in upgrade paths: {', '.join(missing)}")

    closure = (
        _dependency_closure(selected_packages, metadata_by_name, provider_index)
        if include_available_dependencies
        else _selected_closure(selected_packages, provider_index)
    )
    install_order = _install_order(closure, metadata_by_name, provider_index)
    uninstall_order = tuple(reversed(install_order))
    manager = "dnf" if package_format == "rpm" else "apt"

    edges: list[dict[str, str]] = []
    warnings: list[str] = []
    package_records: list[dict[str, Any]] = []
    for package_name in install_order:
        package = metadata_by_name[package_name]
        boundary: list[str] = []
        for requirement in package.requires:
            target = provider_index.get(requirement)
            if target and target in closure and target != package.name:
                edges.append({"source": package.name, "target": target, "require": requirement})
            elif not (target and target in closure):
                boundary.append(requirement)
                warnings.append(f"{package.name} requires {requirement} outside upgrade set")
        package_records.append(
            {
                "name": package.name,
                "artifact": str(package.artifact),
                "provides": list(package.provides),
                "requires": list(package.requires),
                "boundary_requires": boundary,
            }
        )

    actions: list[dict[str, Any]] = []
    for package_name in uninstall_order:
        command = _remove_command(manager, package_name)
        actions.append({"phase": "uninstall", "package": package_name, "command": command})
    for package_name in install_order:
        artifact = str(metadata_by_name[package_name].artifact)
        command = _install_command(manager, artifact)
        actions.append({"phase": "install", "package": package_name, "artifact": artifact, "command": command})

    return {
        "version": 1,
        "format": package_format,
        "manager": manager,
        "selected_packages": list(selected_packages),
        "packages": package_records,
        "dependency_edges": _unique_edges(edges),
        "order": {"uninstall": list(uninstall_order), "install": list(install_order)},
        "actions": actions,
        "warnings": _dedupe(warnings),
    }


def _require_string_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{key} must be a non-empty list of strings")
    return value


def _artifact_candidates(path: Path) -> list[Path]:
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    if path.is_file():
        return [path] if _artifact_format(path) else []
    if path.is_dir():
        return [
            candidate
            for candidate in path.rglob("*")
            if candidate.is_file() and _artifact_format(candidate)
        ]
    return []


def _artifact_format(path: Path) -> str:
    name = path.name
    if name.endswith(".src.rpm"):
        return ""
    if name.endswith(".rpm"):
        return "rpm"
    if name.endswith(".deb"):
        return "deb"
    return ""


def _dependency_closure(
    selected_packages: tuple[str, ...],
    metadata_by_name: dict[str, PackageMetadata],
    provider_index: dict[str, str],
) -> frozenset[str]:
    closure: set[str] = set()
    queue: deque[str] = deque()
    for selected in selected_packages:
        provider = provider_index[selected]
        if provider not in closure:
            closure.add(provider)
            queue.append(provider)

    while queue:
        package_name = queue.popleft()
        for requirement in metadata_by_name[package_name].requires:
            provider = provider_index.get(requirement)
            if provider and provider not in closure:
                closure.add(provider)
                queue.append(provider)
    return frozenset(closure)


def _selected_closure(
    selected_packages: tuple[str, ...],
    provider_index: dict[str, str],
) -> frozenset[str]:
    return frozenset(provider_index[selected] for selected in selected_packages)


def _install_order(
    closure: frozenset[str],
    metadata_by_name: dict[str, PackageMetadata],
    provider_index: dict[str, str],
) -> tuple[str, ...]:
    outgoing: dict[str, set[str]] = {package_name: set() for package_name in closure}
    indegree: dict[str, int] = {package_name: 0 for package_name in closure}
    for package_name in closure:
        for requirement in metadata_by_name[package_name].requires:
            provider = provider_index.get(requirement)
            if provider and provider in closure and provider != package_name:
                if package_name not in outgoing[provider]:
                    outgoing[provider].add(package_name)
                    indegree[package_name] += 1

    ready = deque(sorted(name for name, count in indegree.items() if count == 0))
    ordered: list[str] = []
    while ready:
        package_name = ready.popleft()
        ordered.append(package_name)
        for dependent in sorted(outgoing[package_name]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    if len(ordered) != len(closure):
        raise ValueError("dependency cycle detected in upgrade packages")
    return tuple(ordered)


def _remove_command(manager: str, package_name: str) -> list[str]:
    if manager == "dnf":
        return ["sudo", "dnf", "remove", "-y", package_name]
    return ["sudo", "apt-get", "remove", "-y", package_name]


def _install_command(manager: str, artifact: str) -> list[str]:
    if manager == "dnf":
        return ["sudo", "dnf", "install", "-y", artifact]
    return ["sudo", "apt-get", "install", "-y", artifact]


def _unique_edges(edges: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, str]] = []
    for edge in sorted(edges, key=lambda item: (item["source"], item["target"], item["require"])):
        key = (edge["source"], edge["target"], edge["require"])
        if key not in seen:
            seen.add(key)
            result.append(edge)
    return result


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
