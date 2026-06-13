# RPM/DEB Reinstall Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a safe command-line script that uses the current dependency topology to uninstall existing packages in dependency-safe order and install replacement RPM or DEB packages from user-provided or config-defined paths.

**Architecture:** Add a second CLI inside the existing `specdeps` package. It reads `out/dependencies.json`, computes repository order from internal dependency edges, maps repositories to package names and package directories, creates a deterministic uninstall/install command plan, and executes it only when `--execute` is explicitly passed. The default mode is dry-run and prints the exact commands that would run, so operators can review destructive package actions before touching an environment.

**Tech Stack:** Python 3.9+ standard library, `unittest`, Linux package tools invoked through `subprocess` (`dnf` by default; optional `rpm`, `apt`, and `dpkg` command styles).

---

## File Structure

- Modify `pyproject.toml`: add a `specdeps-reinstall` console entry point.
- Modify `src/specdeps/models.py`: add dataclasses for topology input, package path/name config, and command actions.
- Create `src/specdeps/topology.py`: load and validate `out/dependencies.json`, expose repo package sets and internal edges.
- Create `src/specdeps/order.py`: compute uninstall and install repository order with cycle detection.
- Create `src/specdeps/package_config.py`: read optional JSON config and merge CLI `repo=path` overrides.
- Create `src/specdeps/package_files.py`: discover `.rpm` or `.deb` files below configured directories and group them by repository.
- Create `src/specdeps/reinstall_plan.py`: build safe uninstall/install command actions.
- Create `src/specdeps/reinstall_cli.py`: parse CLI args, print dry-run plan, and execute when requested.
- Create `config/reinstall.example.json`: example config file for package paths and package-manager settings.
- Create `tests/test_topology.py`: topology JSON loading tests.
- Create `tests/test_order.py`: order calculation and cycle tests.
- Create `tests/test_package_config.py`: config and CLI override tests.
- Create `tests/test_package_files.py`: RPM and DEB package file discovery tests.
- Create `tests/test_reinstall_plan.py`: command planning tests.
- Create `tests/test_reinstall_cli.py`: end-to-end dry-run and execute-mode tests with mocked subprocess.

## Behavioral Rules

- A topology edge `A -> B` means package(s) from repository `A` require package(s) from repository `B`.
- Install order is dependency-first: if `A -> B`, install `B` before `A`.
- Uninstall order is reverse dependency-first: if `A -> B`, uninstall `A` before `B`.
- All binary package names listed under each spec's `packages` array are considered owned packages for uninstall.
- Self-dependencies are ignored by order calculation.
- Default mode is dry-run and never executes package-manager commands.
- Real execution requires `--execute`.
- The tool must refuse execution unless every selected repository has at least one package file with the extension implied by the selected manager.
- The tool must print the final uninstall and install commands in the exact order they will run.
- The default package manager is `dnf`; the command form is `sudo dnf remove -y <packages...>` and `sudo dnf install -y <rpm-files...>`.
- `--no-sudo` removes the leading `sudo`.
- `--manager rpm` uses `sudo rpm -e <packages...>` for uninstall and `sudo rpm -Uvh <rpm-files...>` for install.
- `--manager apt` uses `sudo apt-get remove -y <packages...>` for uninstall and `sudo apt-get install -y <deb-files...>` for install.
- `--manager dpkg` uses `sudo dpkg -r <packages...>` for uninstall and `sudo dpkg -i <deb-files...>` for install.
- Config key `package_names` can override uninstall package names per repository when DEB package names differ from names parsed from RPM spec files.
- `--only-repo repo1,repo2` limits actions to those repositories and their internal dependency closure, so required provider repos are included automatically.
- `--skip-uninstall` only installs new packages in install order.
- `--skip-install` only uninstalls old packages in uninstall order.

## Example User Flows

Dry-run from config:

```bash
PYTHONPATH=src python -m specdeps.reinstall_cli --topology out/dependencies.json --config config/reinstall.json
```

Dry-run with CLI paths:

```bash
PYTHONPATH=src python -m specdeps.reinstall_cli --topology out/dependencies.json --package-dir src-openEuler-ummu=/srv/rpms/ummu --package-dir src-openEuler-cdma=/srv/rpms/cdma
```

Dry-run with DEB packages:

```bash
PYTHONPATH=src python -m specdeps.reinstall_cli --topology out/dependencies.json --manager apt --package-dir src-openEuler-ummu=/srv/debs/ummu --package-dir src-openEuler-cdma=/srv/debs/cdma
```

Execute after review:

```bash
PYTHONPATH=src python -m specdeps.reinstall_cli --topology out/dependencies.json --config config/reinstall.json --execute
```

### Task 1: Add Shared Models And Topology Loader

**Files:**
- Modify: `src/specdeps/models.py`
- Create: `src/specdeps/topology.py`
- Create: `tests/test_topology.py`

- [ ] **Step 1: Write failing topology tests**

Create `tests/test_topology.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from specdeps.topology import load_topology


class TopologyTests(unittest.TestCase):
    def test_load_topology_extracts_repos_edges_and_packages(self):
        payload = {
            "repos": ["src-openEuler-cdma", "src-openEuler-ummu"],
            "edges": [
                {
                    "source_repo": "src-openEuler-cdma",
                    "target_repo": "src-openEuler-ummu",
                    "dependency": "libummu",
                }
            ],
            "specs": [
                {
                    "repo": "src-openEuler-cdma",
                    "packages": ["libcdma", "libcdma-devel"],
                    "provides": [],
                    "requires": ["libummu"],
                    "source_name": "libcdma",
                    "spec_path": "work/repos/src-openEuler-cdma/cdma.spec",
                },
                {
                    "repo": "src-openEuler-ummu",
                    "packages": ["libummu", "libummu-devel"],
                    "provides": [],
                    "requires": [],
                    "source_name": "libummu",
                    "spec_path": "work/repos/src-openEuler-ummu/ummu.spec",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            topology_path = Path(tmp) / "dependencies.json"
            topology_path.write_text(json.dumps(payload), encoding="utf-8")

            topology = load_topology(topology_path)

        self.assertEqual(topology.repos, ("src-openEuler-cdma", "src-openEuler-ummu"))
        self.assertEqual(topology.edges, (("src-openEuler-cdma", "src-openEuler-ummu", "libummu"),))
        self.assertEqual(topology.packages_by_repo["src-openEuler-cdma"], ("libcdma", "libcdma-devel"))
        self.assertEqual(topology.packages_by_repo["src-openEuler-ummu"], ("libummu", "libummu-devel"))

    def test_load_topology_rejects_missing_specs(self):
        with tempfile.TemporaryDirectory() as tmp:
            topology_path = Path(tmp) / "dependencies.json"
            topology_path.write_text(json.dumps({"repos": ["src-openEuler-cdma"], "edges": []}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing specs"):
                load_topology(topology_path)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_topology -v
```

Expected:

```text
ModuleNotFoundError: No module named 'specdeps.topology'
```

- [ ] **Step 3: Add dataclasses to `src/specdeps/models.py`**

Append this code to `src/specdeps/models.py`:

```python


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
```

- [ ] **Step 4: Implement topology loading**

Create `src/specdeps/topology.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import TopologyData


def load_topology(path: str | Path) -> TopologyData:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    repos = tuple(_require_list(payload, "repos"))
    edges_payload = _require_list(payload, "edges")
    specs_payload = payload.get("specs")
    if not isinstance(specs_payload, list):
        raise ValueError(f"{path} missing specs")

    edges: list[tuple[str, str, str]] = []
    for index, edge in enumerate(edges_payload):
        if not isinstance(edge, dict):
            raise ValueError(f"edge {index} must be an object")
        edges.append((str(edge["source_repo"]), str(edge["target_repo"]), str(edge["dependency"])))

    packages_by_repo: dict[str, tuple[str, ...]] = {repo: tuple() for repo in repos}
    for index, spec in enumerate(specs_payload):
        if not isinstance(spec, dict):
            raise ValueError(f"spec {index} must be an object")
        repo = str(spec.get("repo", ""))
        packages = spec.get("packages")
        if repo not in packages_by_repo:
            continue
        if not isinstance(packages, list):
            raise ValueError(f"spec {index} packages must be a list")
        packages_by_repo[repo] = tuple(str(package) for package in packages)

    missing = [repo for repo, packages in packages_by_repo.items() if not packages]
    if missing:
        raise ValueError(f"{path} missing package data for: {', '.join(missing)}")

    return TopologyData(
        repos=repos,
        edges=tuple(edges),
        packages_by_repo=packages_by_repo,
    )


def _require_list(payload: dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"topology {key} must be a list")
    return value
```

- [ ] **Step 5: Run topology tests to verify they pass**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_topology -v
```

Expected:

```text
Ran 2 tests

OK
```

- [ ] **Step 6: Commit**

```bash
git add src/specdeps/models.py src/specdeps/topology.py tests/test_topology.py
git commit -m "feat: load reinstall topology data"
```

### Task 2: Repository Order Calculation

**Files:**
- Create: `src/specdeps/order.py`
- Create: `tests/test_order.py`

- [ ] **Step 1: Write failing order tests**

Create `tests/test_order.py`:

```python
import unittest

from specdeps.models import TopologyData
from specdeps.order import dependency_closure, install_order, uninstall_order


class OrderTests(unittest.TestCase):
    def test_install_and_uninstall_order_follow_dependency_edges(self):
        topology = TopologyData(
            repos=("A", "B", "C"),
            edges=(("A", "B", "pkg-b"), ("B", "C", "pkg-c")),
            packages_by_repo={"A": ("a",), "B": ("b",), "C": ("c",)},
        )

        self.assertEqual(install_order(topology, None), ("C", "B", "A"))
        self.assertEqual(uninstall_order(topology, None), ("A", "B", "C"))

    def test_dependency_closure_includes_provider_repos(self):
        topology = TopologyData(
            repos=("A", "B", "C", "D"),
            edges=(("A", "B", "pkg-b"), ("B", "C", "pkg-c")),
            packages_by_repo={"A": ("a",), "B": ("b",), "C": ("c",), "D": ("d",)},
        )

        self.assertEqual(dependency_closure(topology, ("A",)), frozenset({"A", "B", "C"}))
        self.assertEqual(install_order(topology, ("A",)), ("C", "B", "A"))

    def test_cycle_raises_value_error(self):
        topology = TopologyData(
            repos=("A", "B"),
            edges=(("A", "B", "pkg-b"), ("B", "A", "pkg-a")),
            packages_by_repo={"A": ("a",), "B": ("b",)},
        )

        with self.assertRaisesRegex(ValueError, "dependency cycle"):
            install_order(topology, None)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_order -v
```

Expected:

```text
ModuleNotFoundError: No module named 'specdeps.order'
```

- [ ] **Step 3: Implement repository ordering**

Create `src/specdeps/order.py`:

```python
from __future__ import annotations

from collections import defaultdict, deque

from .models import TopologyData


def dependency_closure(topology: TopologyData, selected_repos: tuple[str, ...] | None) -> frozenset[str]:
    if not selected_repos:
        return frozenset(topology.repos)

    known = set(topology.repos)
    requested = set(selected_repos)
    unknown = sorted(requested - known)
    if unknown:
        raise ValueError(f"unknown repositories: {', '.join(unknown)}")

    providers_by_consumer: dict[str, set[str]] = defaultdict(set)
    for source, target, _dependency in topology.edges:
        providers_by_consumer[source].add(target)

    closure = set(requested)
    queue = deque(sorted(requested))
    while queue:
        repo = queue.popleft()
        for provider in sorted(providers_by_consumer.get(repo, set())):
            if provider not in closure:
                closure.add(provider)
                queue.append(provider)
    return frozenset(closure)


def install_order(topology: TopologyData, selected_repos: tuple[str, ...] | None) -> tuple[str, ...]:
    repos = dependency_closure(topology, selected_repos)
    return _topological_order(topology, repos, dependency_first=True)


def uninstall_order(topology: TopologyData, selected_repos: tuple[str, ...] | None) -> tuple[str, ...]:
    repos = dependency_closure(topology, selected_repos)
    return tuple(reversed(_topological_order(topology, repos, dependency_first=True)))


def _topological_order(topology: TopologyData, repos: frozenset[str], dependency_first: bool) -> tuple[str, ...]:
    outgoing: dict[str, set[str]] = {repo: set() for repo in repos}
    indegree: dict[str, int] = {repo: 0 for repo in repos}

    for source, target, _dependency in topology.edges:
        if source not in repos or target not in repos or source == target:
            continue
        before, after = (target, source) if dependency_first else (source, target)
        if after not in outgoing[before]:
            outgoing[before].add(after)
            indegree[after] += 1

    ready = deque(sorted(repo for repo, count in indegree.items() if count == 0))
    ordered: list[str] = []
    while ready:
        repo = ready.popleft()
        ordered.append(repo)
        for dependent in sorted(outgoing[repo]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)

    if len(ordered) != len(repos):
        raise ValueError("dependency cycle detected in topology")
    return tuple(ordered)
```

- [ ] **Step 4: Run order tests to verify they pass**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_order -v
```

Expected:

```text
Ran 3 tests

OK
```

- [ ] **Step 5: Commit**

```bash
git add src/specdeps/order.py tests/test_order.py
git commit -m "feat: calculate reinstall order from topology"
```

### Task 3: Package Path And Name Config Loader

**Files:**
- Create: `src/specdeps/package_config.py`
- Create: `config/reinstall.example.json`
- Create: `tests/test_package_config.py`

- [ ] **Step 1: Write failing config tests**

Create `tests/test_package_config.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from specdeps.package_config import load_package_config


class PackageConfigTests(unittest.TestCase):
    def test_load_config_and_cli_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "reinstall.json"
            config_path.write_text(
                json.dumps(
                    {
                        "manager": "dnf",
                        "sudo": True,
                        "package_dirs": {
                            "src-openEuler-ummu": ["/repo/ummu"],
                            "src-openEuler-cdma": "/repo/cdma-old",
                        },
                        "package_names": {
                            "src-openEuler-cdma": ["libcdma1", "libcdma-dev"]
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_package_config(
                config_path,
                ("src-openEuler-cdma=/repo/cdma-new", "src-openEuler-obmm=/repo/obmm"),
                manager_override="apt",
                sudo_override=False,
            )

        self.assertEqual(config.manager, "apt")
        self.assertFalse(config.sudo)
        self.assertEqual(config.package_dirs["src-openEuler-ummu"], (Path("/repo/ummu"),))
        self.assertEqual(config.package_dirs["src-openEuler-cdma"], (Path("/repo/cdma-new"),))
        self.assertEqual(config.package_dirs["src-openEuler-obmm"], (Path("/repo/obmm"),))
        self.assertEqual(config.package_names["src-openEuler-cdma"], ("libcdma1", "libcdma-dev"))

    def test_rejects_invalid_package_dir_override(self):
        with self.assertRaisesRegex(ValueError, "repo=path"):
            load_package_config(None, ("src-openEuler-cdma",), None, None)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_package_config -v
```

Expected:

```text
ModuleNotFoundError: No module named 'specdeps.package_config'
```

- [ ] **Step 3: Create example config**

Create `config/reinstall.example.json`:

```json
{
  "manager": "dnf",
  "sudo": true,
  "package_dirs": {
    "src-openEuler-cdma": ["/path/to/new-packages/cdma"],
    "src-openEuler-ummu": ["/path/to/new-packages/ummu"]
  },
  "package_names": {
    "src-openEuler-cdma": ["libcdma", "libcdma-devel"]
  }
}
```

- [ ] **Step 4: Implement package config loader**

Create `src/specdeps/package_config.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import PackagePathConfig


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

    sudo = bool(payload.get("sudo", True)) if sudo_override is None else sudo_override
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
```

- [ ] **Step 5: Run config tests to verify they pass**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_package_config -v
```

Expected:

```text
Ran 2 tests

OK
```

- [ ] **Step 6: Commit**

```bash
git add src/specdeps/package_config.py config/reinstall.example.json tests/test_package_config.py
git commit -m "feat: load reinstall package path config"
```

### Task 4: Package File Discovery

**Files:**
- Create: `src/specdeps/package_files.py`
- Create: `tests/test_package_files.py`

- [ ] **Step 1: Write failing package file discovery tests**

Create `tests/test_package_files.py`:

```python
import tempfile
import unittest
from pathlib import Path

from specdeps.package_files import discover_package_files


class PackageFileTests(unittest.TestCase):
    def test_discovers_rpms_from_multiple_dirs_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            (first / "libummu-devel-1.0-1.aarch64.rpm").write_text("", encoding="utf-8")
            (second / "libummu-1.0-1.aarch64.rpm").write_text("", encoding="utf-8")
            (second / "libummu_1.0_arm64.deb").write_text("", encoding="utf-8")
            (second / "notes.txt").write_text("", encoding="utf-8")

            packages = discover_package_files({"src-openEuler-ummu": (first, second)}, ".rpm")

        self.assertEqual(
            [path.name for path in packages["src-openEuler-ummu"]],
            ["libummu-1.0-1.aarch64.rpm", "libummu-devel-1.0-1.aarch64.rpm"],
        )

    def test_discovers_debs_when_extension_is_deb(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.mkdir(exist_ok=True)
            (root / "libummu_1.0_arm64.deb").write_text("", encoding="utf-8")
            (root / "libummu-1.0-1.aarch64.rpm").write_text("", encoding="utf-8")

            packages = discover_package_files({"src-openEuler-ummu": (root,)}, ".deb")

        self.assertEqual([path.name for path in packages["src-openEuler-ummu"]], ["libummu_1.0_arm64.deb"])

    def test_missing_directory_raises(self):
        with self.assertRaisesRegex(FileNotFoundError, "does not exist"):
            discover_package_files({"src-openEuler-ummu": (Path("/missing/packages"),)}, ".rpm")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_package_files -v
```

Expected:

```text
ModuleNotFoundError: No module named 'specdeps.package_files'
```

- [ ] **Step 3: Implement package file discovery**

Create `src/specdeps/package_files.py`:

```python
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
            repo_packages.extend(path for path in directory.rglob(f"*{extension}") if path.is_file())
        packages_by_repo[repo] = tuple(sorted(repo_packages, key=lambda path: path.name))
    return packages_by_repo
```

- [ ] **Step 4: Run package file discovery tests to verify they pass**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_package_files -v
```

Expected:

```text
Ran 3 tests

OK
```

- [ ] **Step 5: Commit**

```bash
git add src/specdeps/package_files.py tests/test_package_files.py
git commit -m "feat: discover replacement package files"
```

### Task 5: Reinstall Command Planner

**Files:**
- Create: `src/specdeps/reinstall_plan.py`
- Create: `tests/test_reinstall_plan.py`

- [ ] **Step 1: Write failing command planner tests**

Create `tests/test_reinstall_plan.py`:

```python
import unittest
from pathlib import Path

from specdeps.models import PackagePathConfig, TopologyData
from specdeps.reinstall_plan import build_reinstall_actions, package_extension


class ReinstallPlanTests(unittest.TestCase):
    def test_builds_uninstall_then_install_actions(self):
        topology = TopologyData(
            repos=("app", "lib"),
            edges=(("app", "lib", "libpkg"),),
            packages_by_repo={"app": ("app", "app-devel"), "lib": ("libpkg",)},
        )
        config = PackagePathConfig(
            package_dirs={"app": (Path("/rpms/app"),), "lib": (Path("/rpms/lib"),)},
            package_names={},
            manager="dnf",
            sudo=True,
        )
        package_files_by_repo = {
            "app": (Path("/rpms/app/app-1.rpm"),),
            "lib": (Path("/rpms/lib/libpkg-1.rpm"),),
        }

        actions = build_reinstall_actions(topology, config, package_files_by_repo, None, False, False)

        self.assertEqual(
            [(action.phase, action.repo, action.command) for action in actions],
            [
                ("uninstall", "app", ("sudo", "dnf", "remove", "-y", "app", "app-devel")),
                ("uninstall", "lib", ("sudo", "dnf", "remove", "-y", "libpkg")),
                ("install", "lib", ("sudo", "dnf", "install", "-y", "/rpms/lib/libpkg-1.rpm")),
                ("install", "app", ("sudo", "dnf", "install", "-y", "/rpms/app/app-1.rpm")),
            ],
        )

    def test_no_sudo_rpm_manager_and_skip_uninstall(self):
        topology = TopologyData(
            repos=("app",),
            edges=(),
            packages_by_repo={"app": ("app",)},
        )
        config = PackagePathConfig(package_dirs={"app": (Path("/rpms/app"),)}, package_names={}, manager="rpm", sudo=False)
        package_files_by_repo = {"app": (Path("/rpms/app/app-1.rpm"),)}

        actions = build_reinstall_actions(topology, config, package_files_by_repo, ("app",), True, False)

        self.assertEqual(actions[0].command, ("rpm", "-Uvh", "/rpms/app/app-1.rpm"))

    def test_apt_manager_uses_deb_files_and_package_name_overrides(self):
        topology = TopologyData(
            repos=("app",),
            edges=(),
            packages_by_repo={"app": ("rpm-app-name",)},
        )
        config = PackagePathConfig(
            package_dirs={"app": (Path("/debs/app"),)},
            package_names={"app": ("deb-app-name",)},
            manager="apt",
            sudo=True,
        )
        package_files_by_repo = {"app": (Path("/debs/app/deb-app-name_1.0_arm64.deb"),)}

        actions = build_reinstall_actions(topology, config, package_files_by_repo, ("app",), False, False)

        self.assertEqual(actions[0].command, ("sudo", "apt-get", "remove", "-y", "deb-app-name"))
        self.assertEqual(actions[1].command, ("sudo", "apt-get", "install", "-y", "/debs/app/deb-app-name_1.0_arm64.deb"))
        self.assertEqual(package_extension("apt"), ".deb")

    def test_dpkg_manager_uses_dpkg_commands(self):
        topology = TopologyData(
            repos=("app",),
            edges=(),
            packages_by_repo={"app": ("app",)},
        )
        config = PackagePathConfig(package_dirs={"app": (Path("/debs/app"),)}, package_names={}, manager="dpkg", sudo=False)
        package_files_by_repo = {"app": (Path("/debs/app/app_1.0_arm64.deb"),)}

        actions = build_reinstall_actions(topology, config, package_files_by_repo, ("app",), False, False)

        self.assertEqual(actions[0].command, ("dpkg", "-r", "app"))
        self.assertEqual(actions[1].command, ("dpkg", "-i", "/debs/app/app_1.0_arm64.deb"))

    def test_missing_package_files_for_selected_repo_raises(self):
        topology = TopologyData(
            repos=("app",),
            edges=(),
            packages_by_repo={"app": ("app",)},
        )
        config = PackagePathConfig(package_dirs={}, package_names={}, manager="dnf", sudo=True)

        with self.assertRaisesRegex(ValueError, "missing package files"):
            build_reinstall_actions(topology, config, {}, None, False, False)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_reinstall_plan -v
```

Expected:

```text
ModuleNotFoundError: No module named 'specdeps.reinstall_plan'
```

- [ ] **Step 3: Implement reinstall command planner**

Create `src/specdeps/reinstall_plan.py`:

```python
from __future__ import annotations

from pathlib import Path

from .models import CommandAction, PackagePathConfig, TopologyData
from .order import install_order, uninstall_order


def build_reinstall_actions(
    topology: TopologyData,
    config: PackagePathConfig,
    package_files_by_repo: dict[str, tuple[Path, ...]],
    selected_repos: tuple[str, ...] | None,
    skip_uninstall: bool,
    skip_install: bool,
) -> tuple[CommandAction, ...]:
    install_repos = install_order(topology, selected_repos)
    uninstall_repos = uninstall_order(topology, selected_repos)
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
```

- [ ] **Step 4: Run command planner tests to verify they pass**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_reinstall_plan -v
```

Expected:

```text
Ran 5 tests

OK
```

- [ ] **Step 5: Commit**

```bash
git add src/specdeps/reinstall_plan.py tests/test_reinstall_plan.py
git commit -m "feat: plan topology-based reinstall commands"
```

### Task 6: Reinstall CLI

**Files:**
- Modify: `pyproject.toml`
- Create: `src/specdeps/reinstall_cli.py`
- Create: `tests/test_reinstall_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_reinstall_cli.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from specdeps.reinstall_cli import main


class ReinstallCliTests(unittest.TestCase):
    def test_dry_run_prints_commands_without_executing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            topology = root / "dependencies.json"
            repo_dir = root / "rpms" / "lib"
            repo_dir.mkdir(parents=True)
            (repo_dir / "libpkg-1.rpm").write_text("", encoding="utf-8")
            topology.write_text(
                json.dumps(
                    {
                        "repos": ["lib"],
                        "edges": [],
                        "specs": [
                            {
                                "repo": "lib",
                                "packages": ["libpkg"],
                                "provides": [],
                                "requires": [],
                                "source_name": "libpkg",
                                "spec_path": "lib.spec",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch("specdeps.reinstall_cli.subprocess.run") as run, patch("builtins.print") as print_:
                exit_code = main(
                    [
                        "--topology",
                        str(topology),
                        "--package-dir",
                        f"lib={repo_dir}",
                    ]
                )

        self.assertEqual(exit_code, 0)
        run.assert_not_called()
        printed = "\n".join(" ".join(str(part) for part in call.args) for call in print_.call_args_list)
        self.assertIn("DRY-RUN", printed)
        self.assertIn("sudo dnf remove -y libpkg", printed)
        self.assertIn("sudo dnf install -y", printed)

    def test_execute_runs_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            topology = root / "dependencies.json"
            repo_dir = root / "rpms" / "lib"
            repo_dir.mkdir(parents=True)
            rpm = repo_dir / "libpkg-1.rpm"
            rpm.write_text("", encoding="utf-8")
            topology.write_text(
                json.dumps(
                    {
                        "repos": ["lib"],
                        "edges": [],
                        "specs": [
                            {
                                "repo": "lib",
                                "packages": ["libpkg"],
                                "provides": [],
                                "requires": [],
                                "source_name": "libpkg",
                                "spec_path": "lib.spec",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch("specdeps.reinstall_cli.subprocess.run") as run:
                exit_code = main(
                    [
                        "--topology",
                        str(topology),
                        "--package-dir",
                        f"lib={repo_dir}",
                        "--execute",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(run.call_count, 2)
        run.assert_any_call(("sudo", "dnf", "remove", "-y", "libpkg"), check=True)
        run.assert_any_call(("sudo", "dnf", "install", "-y", str(rpm)), check=True)

    def test_apt_dry_run_discovers_deb_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            topology = root / "dependencies.json"
            repo_dir = root / "debs" / "lib"
            repo_dir.mkdir(parents=True)
            (repo_dir / "libpkg_1.0_arm64.deb").write_text("", encoding="utf-8")
            (repo_dir / "libpkg-1.rpm").write_text("", encoding="utf-8")
            topology.write_text(
                json.dumps(
                    {
                        "repos": ["lib"],
                        "edges": [],
                        "specs": [
                            {
                                "repo": "lib",
                                "packages": ["libpkg"],
                                "provides": [],
                                "requires": [],
                                "source_name": "libpkg",
                                "spec_path": "lib.spec",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch("specdeps.reinstall_cli.subprocess.run") as run, patch("builtins.print") as print_:
                exit_code = main(
                    [
                        "--topology",
                        str(topology),
                        "--manager",
                        "apt",
                        "--package-dir",
                        f"lib={repo_dir}",
                    ]
                )

        self.assertEqual(exit_code, 0)
        run.assert_not_called()
        printed = "\n".join(" ".join(str(part) for part in call.args) for call in print_.call_args_list)
        self.assertIn("sudo apt-get remove -y libpkg", printed)
        self.assertIn("sudo apt-get install -y", printed)
        self.assertIn("libpkg_1.0_arm64.deb", printed)
        self.assertNotIn("libpkg-1.rpm", printed)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_reinstall_cli -v
```

Expected:

```text
ModuleNotFoundError: No module named 'specdeps.reinstall_cli'
```

- [ ] **Step 3: Add console entry point**

Modify `pyproject.toml` `[project.scripts]` to:

```toml
[project.scripts]
specdeps = "specdeps.cli:main"
specdeps-reinstall = "specdeps.reinstall_cli:main"
```

- [ ] **Step 4: Implement reinstall CLI**

Create `src/specdeps/reinstall_cli.py`:

```python
from __future__ import annotations

import argparse
import shlex
import subprocess

from .package_config import load_package_config
from .package_files import discover_package_files
from .reinstall_plan import build_reinstall_actions, package_extension
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
    parser.add_argument("--execute", action="store_true", help="Execute commands; default is dry-run")
    args = parser.parse_args(argv)

    selected_repos = _parse_only_repo(args.only_repo)
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
    )

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
```

- [ ] **Step 5: Run CLI tests to verify they pass**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_reinstall_cli -v
```

Expected:

```text
Ran 3 tests

OK
```

- [ ] **Step 6: Run full test suite**

Run:

```bash
PYTHONPATH=src python -m unittest discover -v
```

Expected:

```text
Ran 33 tests

OK
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/specdeps/reinstall_cli.py tests/test_reinstall_cli.py
git commit -m "feat: add topology-based reinstall cli"
```

### Task 7: Validate Against Current Topology In Dry-Run Mode

**Files:**
- Read: `out/dependencies.json`
- Read: `config/reinstall.example.json`
- Generate: temporary fixture RPM directories under `/tmp/specdeps-rpms`
- Generate: temporary fixture DEB directories under `/tmp/specdeps-debs`

- [ ] **Step 1: Create fixture RPM directories for all current topology repositories**

Run:

```bash
python -c "import json, pathlib; data=json.load(open('out/dependencies.json', encoding='utf-8')); root=pathlib.Path('/tmp/specdeps-rpms'); root.mkdir(exist_ok=True); [((root/repo).mkdir(exist_ok=True), (root/repo/f'{repo}-fixture-1.0-1.noarch.rpm').write_text('')) for repo in data['repos']]"
```

Expected:

```text
```

- [ ] **Step 2: Create fixture DEB directories for all current topology repositories**

Run:

```bash
python -c "import json, pathlib; data=json.load(open('out/dependencies.json', encoding='utf-8')); root=pathlib.Path('/tmp/specdeps-debs'); root.mkdir(exist_ok=True); [((root/repo).mkdir(exist_ok=True), (root/repo/f'{repo}_fixture_1.0_all.deb').write_text('')) for repo in data['repos']]"
```

Expected:

```text
```

- [ ] **Step 3: Dry-run full RPM reinstall using CLI paths**

Run:

```bash
PYTHONPATH=src python -m specdeps.reinstall_cli --topology out/dependencies.json --package-dir src-openEuler-cdma=/tmp/specdeps-rpms/src-openEuler-cdma --package-dir src-openEuler-libvirt=/tmp/specdeps-rpms/src-openEuler-libvirt --package-dir src-openEuler-memlink=/tmp/specdeps-rpms/src-openEuler-memlink --package-dir src-openEuler-obmm=/tmp/specdeps-rpms/src-openEuler-obmm --package-dir src-openEuler-qemu=/tmp/specdeps-rpms/src-openEuler-qemu --package-dir src-openEuler-sysSentry=/tmp/specdeps-rpms/src-openEuler-sysSentry --package-dir src-openEuler-ubctl=/tmp/specdeps-rpms/src-openEuler-ubctl --package-dir src-openEuler-ubs-comm=/tmp/specdeps-rpms/src-openEuler-ubs-comm --package-dir src-openEuler-ubs-engine=/tmp/specdeps-rpms/src-openEuler-ubs-engine --package-dir src-openEuler-ubs-mem=/tmp/specdeps-rpms/src-openEuler-ubs-mem --package-dir src-openEuler-ubturbo=/tmp/specdeps-rpms/src-openEuler-ubturbo --package-dir src-openEuler-ubutils=/tmp/specdeps-rpms/src-openEuler-ubutils --package-dir src-openEuler-umdk=/tmp/specdeps-rpms/src-openEuler-umdk --package-dir src-openEuler-ummu=/tmp/specdeps-rpms/src-openEuler-ummu
```

Expected output begins with:

```text
DRY-RUN
1. [uninstall]
```

Expected output contains install actions for dependency providers before consumers:

```text
[install] src-openEuler-ummu:
[install] src-openEuler-qemu:
[install] src-openEuler-obmm:
```

- [ ] **Step 4: Dry-run selected RPM repository closure**

Run:

```bash
PYTHONPATH=src python -m specdeps.reinstall_cli --topology out/dependencies.json --only-repo src-openEuler-cdma --package-dir src-openEuler-cdma=/tmp/specdeps-rpms/src-openEuler-cdma --package-dir src-openEuler-ummu=/tmp/specdeps-rpms/src-openEuler-ummu
```

Expected output:

```text
DRY-RUN
1. [uninstall] src-openEuler-cdma: sudo dnf remove -y libcdma libcdma-devel
2. [uninstall] src-openEuler-ummu: sudo dnf remove -y libummu libummu-devel
3. [install] src-openEuler-ummu: sudo dnf install -y /tmp/specdeps-rpms/src-openEuler-ummu/src-openEuler-ummu-fixture-1.0-1.noarch.rpm
4. [install] src-openEuler-cdma: sudo dnf install -y /tmp/specdeps-rpms/src-openEuler-cdma/src-openEuler-cdma-fixture-1.0-1.noarch.rpm
```

- [ ] **Step 5: Dry-run selected DEB repository closure**

Run:

```bash
PYTHONPATH=src python -m specdeps.reinstall_cli --topology out/dependencies.json --manager apt --only-repo src-openEuler-cdma --package-dir src-openEuler-cdma=/tmp/specdeps-debs/src-openEuler-cdma --package-dir src-openEuler-ummu=/tmp/specdeps-debs/src-openEuler-ummu
```

Expected output:

```text
DRY-RUN
1. [uninstall] src-openEuler-cdma: sudo apt-get remove -y libcdma libcdma-devel
2. [uninstall] src-openEuler-ummu: sudo apt-get remove -y libummu libummu-devel
3. [install] src-openEuler-ummu: sudo apt-get install -y /tmp/specdeps-debs/src-openEuler-ummu/src-openEuler-ummu_fixture_1.0_all.deb
4. [install] src-openEuler-cdma: sudo apt-get install -y /tmp/specdeps-debs/src-openEuler-cdma/src-openEuler-cdma_fixture_1.0_all.deb
```

- [ ] **Step 6: Commit validation docs if command examples are added to README**

If a README is created during implementation, run:

```bash
git add README.md
git commit -m "docs: document package reinstall workflow"
```

If no README is created, skip this step.

## Self-Review

**Spec coverage:** The plan covers reading the current topology JSON, deriving uninstall/install order from internal dependency edges, uninstalling existing packages, installing new RPM or DEB packages from config or CLI paths, supporting dry-run and explicit execution, limiting by selected repositories, and testing all behavior without running real package-manager commands.

**Scope check:** The request is one cohesive CLI feature inside the existing topology tool. It does not need to be split into independent subsystem plans.

**Safety check:** The plan defaults to dry-run, prints exact commands, requires `--execute` for real changes, and keeps all tests mocked or fixture-based.

**Placeholder scan:** The plan contains exact file paths, exact commands, full test code, full implementation code, and concrete expected outputs.

**Type consistency:** `TopologyData`, `PackagePathConfig`, and `CommandAction` are defined in `src/specdeps/models.py` and referenced consistently by topology loading, order calculation, config loading, package file discovery, command planning, and CLI code.
