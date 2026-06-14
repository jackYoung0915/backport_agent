from __future__ import annotations

from pathlib import Path

from ..models import CommandAction, PackagePathConfig, TopologyData
from .order import external_dependents, install_order, uninstall_order


def build_reinstall_actions(
    topology: TopologyData,
    config: PackagePathConfig,
    package_files_by_repo: dict[str, tuple[Path, ...]],
    selected_repos: tuple[str, ...] | None,
    skip_uninstall: bool,
    skip_install: bool,
    *,
    allow_external_dependents: bool = False,
) -> tuple[CommandAction, ...]:
    if skip_uninstall and skip_install:
        raise ValueError("cannot skip both uninstall and install")

    install_repos = install_order(topology, selected_repos)
    uninstall_repos = uninstall_order(topology, selected_repos)
    if not skip_uninstall and not allow_external_dependents:
        _assert_no_external_dependents(topology, selected_repos)
    if not skip_install:
        _assert_package_files_present(install_repos, package_files_by_repo)

    actions: list[CommandAction] = []
    if not skip_uninstall:
        for repo in uninstall_repos:
            packages = config.package_names.get(repo, topology.packages_by_repo[repo])
            if packages:
                actions.append(CommandAction("uninstall", repo, _remove_command(config, packages)))

    if not skip_install:
        for repo in install_repos:
            package_files = tuple(str(path) for path in package_files_by_repo.get(repo, ()))
            actions.append(CommandAction("install", repo, _install_command(config, package_files)))

    return tuple(actions)


def package_extension(manager: str) -> str:
    if manager in {"dnf", "rpm"}:
        return ".rpm"
    if manager in {"apt", "dpkg"}:
        return ".deb"
    raise ValueError("manager must be dnf, rpm, apt, or dpkg")


def _assert_no_external_dependents(
    topology: TopologyData,
    selected_repos: tuple[str, ...] | None,
) -> None:
    dependents = external_dependents(topology, selected_repos)
    if not dependents:
        return
    details = "; ".join(
        f"{repo} required by {', '.join(dependent_repos)}"
        for repo, dependent_repos in dependents.items()
    )
    raise ValueError(f"dependents outside reinstall set: {details}")


def _assert_package_files_present(repos: tuple[str, ...], package_files_by_repo: dict[str, tuple[Path, ...]]) -> None:
    missing = [repo for repo in repos if not package_files_by_repo.get(repo)]
    if missing:
        raise ValueError(f"missing package files for repositories: {', '.join(missing)}")


def _base(config: PackagePathConfig) -> tuple[str, ...]:
    command = "apt-get" if config.manager == "apt" else config.manager
    return ("sudo", command) if config.sudo else (command,)


def _remove_command(config: PackagePathConfig, packages: tuple[str, ...]) -> tuple[str, ...]:
    if config.manager == "dnf":
        return _base(config) + ("remove", "-y") + packages
    if config.manager == "rpm":
        return _base(config) + ("-e",) + packages
    if config.manager == "apt":
        return _base(config) + ("remove", "-y") + packages
    return _base(config) + ("-r",) + packages


def _install_command(config: PackagePathConfig, package_files: tuple[str, ...]) -> tuple[str, ...]:
    if config.manager == "dnf":
        return _base(config) + ("install", "-y") + package_files
    if config.manager == "rpm":
        return _base(config) + ("-Uvh",) + package_files
    if config.manager == "apt":
        return _base(config) + ("install", "-y") + package_files
    return _base(config) + ("-i",) + package_files
