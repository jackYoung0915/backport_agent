from __future__ import annotations

import argparse
import shlex
import subprocess

from .package_config import load_package_config
from .package_files import discover_package_files
from .plan import build_reinstall_actions, package_extension
from .topology import load_topology


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Uninstall and reinstall RPM/DEB packages using spec dependency topology")
    parser.add_argument("--topology", default="out/dependencies.json", help="Path to topology JSON")
    parser.add_argument("--config", help="Path to reinstall config JSON")
    parser.add_argument("--package-dir", action="append", default=[], help="Repository package path override: repo=/path")
    parser.add_argument("--manager", choices=["dnf", "rpm", "apt", "dpkg"], help="Package manager command style")
    parser.add_argument("--no-sudo", action="store_true", help="Do not prefix package commands with sudo")
    parser.add_argument("--only-repo", help="Comma-separated repository names to reinstall plus dependency closure")
    parser.add_argument("--skip-uninstall", action="store_true", help="Only install replacement packages")
    parser.add_argument("--skip-install", action="store_true", help="Only uninstall existing packages")
    parser.add_argument(
        "--allow-external-dependents",
        action="store_true",
        help="Allow uninstalling a selected provider while non-selected repositories depend on it",
    )
    parser.add_argument("--execute", action="store_true", help="Execute commands; default is dry-run")
    args = parser.parse_args(argv)
    if args.skip_uninstall and args.skip_install:
        parser.error("--skip-uninstall and --skip-install cannot be used together")

    selected_repos = _parse_only_repo(args.only_repo)
    try:
        topology = load_topology(args.topology)
        config = load_package_config(
            args.config,
            tuple(args.package_dir),
            args.manager,
            False if args.no_sudo else None,
        )
        package_files_by_repo = discover_package_files(config.package_dirs, package_extension(config.manager))
        actions = build_reinstall_actions(
            topology,
            config,
            package_files_by_repo,
            selected_repos,
            args.skip_uninstall,
            args.skip_install,
            allow_external_dependents=args.allow_external_dependents,
        )
    except (FileNotFoundError, NotADirectoryError, ValueError) as error:
        parser.error(str(error))

    print("EXECUTE" if args.execute else "DRY-RUN")
    for index, action in enumerate(actions, start=1):
        print(f"{index}. [{action.phase}] {action.repo}: {_format_command(action.command)}")

    if args.execute:
        for action in actions:
            subprocess.run(action.command, check=True)
    return 0


def _parse_only_repo(value: str | None) -> tuple[str, ...] | None:
    if not value:
        return None
    repos = tuple(repo.strip() for repo in value.split(",") if repo.strip())
    return repos or None


def _format_command(command: tuple[str, ...]) -> str:
    return " ".join(shlex.quote(part) for part in command)


if __name__ == "__main__":
    raise SystemExit(main())
