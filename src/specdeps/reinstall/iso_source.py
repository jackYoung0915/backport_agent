from __future__ import annotations

import platform
import re
from pathlib import Path
from typing import Any

from .json_plan import build_reinstall_json, discover_upgrade_artifacts
from .package_metadata import PackageMetadata, load_package_metadata


def build_iso_reinstall_json(
    selected_packages: tuple[str, ...],
    source_paths: tuple[Path, ...],
) -> dict[str, Any]:
    package_format, artifacts = discover_iso_artifacts(source_paths)
    metadata = [load_package_metadata(artifact, package_format) for artifact in artifacts]
    selected_metadata = select_requested_packages(selected_packages, metadata)
    return build_reinstall_json(
        selected_packages,
        selected_metadata,
        package_format,
        include_available_dependencies=False,
    )


def discover_iso_artifacts(source_paths: tuple[Path, ...]) -> tuple[str, tuple[Path, ...]]:
    return discover_upgrade_artifacts(source_paths)


def select_requested_packages(
    selected_packages: tuple[str, ...],
    packages: list[PackageMetadata],
) -> list[PackageMetadata]:
    selected: list[PackageMetadata] = []
    for requested in selected_packages:
        candidates = [package for package in packages if package.name == requested]
        if not candidates:
            candidates = [package for package in packages if requested in package.provides]
        if not candidates:
            raise ValueError(f"upgrade package not found in ISO source: {requested}")
        selected.append(_best_candidate(candidates))
    return selected


def _best_candidate(candidates: list[PackageMetadata]) -> PackageMetadata:
    return sorted(candidates, key=_candidate_key, reverse=True)[0]


def _candidate_key(package: PackageMetadata) -> tuple[int, tuple[Any, ...], str]:
    return (_arch_rank(package.arch), _version_key(package), str(package.artifact))


def _arch_rank(arch: str) -> int:
    normalized_arch = _normalize_arch(arch)
    current_arch = _normalize_arch(platform.machine())
    if normalized_arch == current_arch:
        return 2
    if normalized_arch in {"noarch", "all", ""}:
        return 1
    return 0


def _normalize_arch(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "arm64": "aarch64",
    }
    return aliases.get(normalized, normalized)


def _version_key(package: PackageMetadata) -> tuple[Any, ...]:
    epoch = _int_or_zero(package.epoch)
    version = _split_version(package.version)
    release = _split_version(package.release)
    return (epoch, version, release)


def _int_or_zero(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def _split_version(value: str) -> tuple[tuple[int, Any], ...]:
    parts: list[tuple[int, Any]] = []
    for part in re.findall(r"[0-9]+|[A-Za-z]+", value):
        if part.isdigit():
            parts.append((1, int(part)))
        else:
            parts.append((0, part.lower()))
    return tuple(parts)
