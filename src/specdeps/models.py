from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class RepoRef:
    name: str
    page_url: str
    clone_url: str
    branch: str


@dataclass(frozen=True)
class SpecInfo:
    repo: str
    spec_path: str
    source_name: str
    packages: frozenset[str]
    provides: frozenset[str]
    requires: tuple[str, ...]


@dataclass(frozen=True, order=True)
class DependencyEdge:
    source_repo: str
    target_repo: str
    dependency: str


@dataclass(frozen=True)
class DependencyGraph:
    repos: tuple[str, ...]
    edges: tuple[DependencyEdge, ...]
    external_requires: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


RepoPaths = Mapping[str, Path]


@dataclass(frozen=True)
class TopologyData:
    repos: tuple[str, ...]
    edges: tuple[tuple[str, str, str], ...]
    packages_by_repo: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class PackagePathConfig:
    package_dirs: Mapping[str, tuple[Path, ...]]
    package_names: Mapping[str, tuple[str, ...]]
    manager: str = "dnf"
    sudo: bool = True


@dataclass(frozen=True)
class CommandAction:
    phase: str
    repo: str
    command: tuple[str, ...]
