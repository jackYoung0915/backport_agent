# Spec Install Dependency Topology Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible tool that fetches the listed GitCode RPM spec repositories, extracts runtime install dependencies, and renders an internal dependency topology graph.

**Architecture:** A small Python package reads a checked-in repository list, clones the target branches, parses `.spec` files with focused RPM-spec heuristics, maps `Requires*` dependencies to packages provided by the same repository set, and writes Mermaid, DOT, JSON, and Markdown outputs. Runtime install dependencies are parsed from `Requires`, `Requires(pre)`, `Requires(post)`, `Requires(preun)`, `Requires(postun)`, and related `Requires(...)` forms; `BuildRequires` is excluded because it is a build-time dependency, not an install dependency.

**Tech Stack:** Python 3.11+ standard library, `unittest`, Git CLI, Mermaid, Graphviz DOT.

---

## File Structure

- Create `pyproject.toml`: package metadata and console entry point.
- Create `config/repos.json`: exact GitCode repository pages and branches supplied by the user.
- Create `src/specdeps/__init__.py`: package marker and version.
- Create `src/specdeps/models.py`: dataclasses shared across parser, graph, and report code.
- Create `src/specdeps/repo_config.py`: repository URL normalization and config loading.
- Create `src/specdeps/spec_parser.py`: RPM spec macro expansion, package/provide discovery, and install dependency extraction.
- Create `src/specdeps/fetcher.py`: shallow clone/update of the requested branches and `.spec` discovery.
- Create `src/specdeps/graph.py`: internal dependency edge construction plus Mermaid/DOT/JSON renderers.
- Create `src/specdeps/report.py`: Markdown report renderer.
- Create `src/specdeps/cli.py`: command-line orchestration.
- Create `tests/test_repo_config.py`: repo URL/config tests.
- Create `tests/test_spec_parser.py`: spec parser tests.
- Create `tests/test_graph.py`: graph construction and renderer tests.
- Create `tests/test_cli.py`: end-to-end offline test with fixture repositories.

## Dependency Interpretation Rules

- A repository node is the GitCode repository name, such as `src-openEuler-qemu`.
- A repository can produce multiple binary package names: `Name:`, `%package suffix`, `%package -n explicit-name`, and names from `Provides:`.
- An install dependency is any package name extracted from `Requires:` or `Requires(...)`.
- A graph edge `A --> B` means a package produced by repository `A` has an install dependency satisfied by a package or provide from repository `B`.
- Dependencies not satisfied by the listed repositories are written to `external_requires` in `out/dependencies.json` and summarized in `out/dependency-report.md`.
- Self-dependencies are omitted from the topology graph because they do not affect ordering among the listed repositories.

## Target Repositories

The checked-in config must contain these exact repository page URLs:

```json
[
  "https://gitcode.com/kunpengcompute/src-openEuler-umdk/tree/openEuler-24.03-LTS-SP3_velinux",
  "https://gitcode.com/kunpengcompute/src-openEuler-ubs-engine/tree/openEuler-24.03-LTS-SP3_velinux_poc",
  "https://gitcode.com/kunpengcompute/src-openEuler-ubturbo/tree/openEuler-24.03-LTS-SP3_velinux_poc",
  "https://gitcode.com/kunpengcompute/src-openEuler-libvirt/tree/openEuler-24.03-LTS-SP3_velinux",
  "https://gitcode.com/kunpengcompute/src-openEuler-memlink/tree/openEuler-24.03-LTS-SP3_velinux",
  "https://gitcode.com/kunpengcompute/src-openEuler-qemu/tree/openEuler-24.03-LTS-SP3_velinux",
  "https://gitcode.com/kunpengcompute/src-openEuler-ubs-comm/tree/openEuler-24.03-LTS-SP3_velinux",
  "https://gitcode.com/kunpengcompute/src-openEuler-ubs-mem/tree/openEuler-24.03-LTS-SP3_velinux",
  "https://gitcode.com/kunpengcompute/src-openEuler-obmm/tree/openEuler-24.03-LTS-SP3_velinux",
  "https://gitcode.com/kunpengcompute/src-openEuler-ubutils/tree/openEuler-24.03-LTS-SP3_velinux",
  "https://gitcode.com/kunpengcompute/src-openEuler-ubctl/tree/openEuler-24.03-LTS-SP3_velinux",
  "https://gitcode.com/kunpengcompute/src-openEuler-ummu/tree/openEuler-24.03-LTS-SP3_velinux",
  "https://gitcode.com/kunpengcompute/src-openEuler-cdma/tree/openEuler-24.03-LTS-SP3_velinux",
  "https://gitcode.com/kunpengcompute/src-openEuler-sysSentry/tree/openEuler-24.03-LTS-SP3_velinux"
]
```

### Task 1: Project Bootstrap And Repository Config

**Files:**
- Create: `pyproject.toml`
- Create: `config/repos.json`
- Create: `src/specdeps/__init__.py`

- [ ] **Step 1: Initialize the local Git repository**

Run:

```bash
git init
```

Expected one of:

```text
Initialized empty Git repository in <workspace>/.git/
```

or:

```text
Reinitialized existing Git repository in <workspace>/.git/
```

- [ ] **Step 2: Create `pyproject.toml`**

Write this exact file:

```toml
[project]
name = "specdeps"
version = "0.1.0"
description = "Extract install dependency topology from RPM spec repositories"
requires-python = ">=3.11"
dependencies = []

[project.scripts]
specdeps = "specdeps.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]
```

- [ ] **Step 3: Create `config/repos.json`**

Write this exact file:

```json
[
  {
    "url": "https://gitcode.com/kunpengcompute/src-openEuler-umdk/tree/openEuler-24.03-LTS-SP3_velinux"
  },
  {
    "url": "https://gitcode.com/kunpengcompute/src-openEuler-ubs-engine/tree/openEuler-24.03-LTS-SP3_velinux_poc"
  },
  {
    "url": "https://gitcode.com/kunpengcompute/src-openEuler-ubturbo/tree/openEuler-24.03-LTS-SP3_velinux_poc"
  },
  {
    "url": "https://gitcode.com/kunpengcompute/src-openEuler-libvirt/tree/openEuler-24.03-LTS-SP3_velinux"
  },
  {
    "url": "https://gitcode.com/kunpengcompute/src-openEuler-memlink/tree/openEuler-24.03-LTS-SP3_velinux"
  },
  {
    "url": "https://gitcode.com/kunpengcompute/src-openEuler-qemu/tree/openEuler-24.03-LTS-SP3_velinux"
  },
  {
    "url": "https://gitcode.com/kunpengcompute/src-openEuler-ubs-comm/tree/openEuler-24.03-LTS-SP3_velinux"
  },
  {
    "url": "https://gitcode.com/kunpengcompute/src-openEuler-ubs-mem/tree/openEuler-24.03-LTS-SP3_velinux"
  },
  {
    "url": "https://gitcode.com/kunpengcompute/src-openEuler-obmm/tree/openEuler-24.03-LTS-SP3_velinux"
  },
  {
    "url": "https://gitcode.com/kunpengcompute/src-openEuler-ubutils/tree/openEuler-24.03-LTS-SP3_velinux"
  },
  {
    "url": "https://gitcode.com/kunpengcompute/src-openEuler-ubctl/tree/openEuler-24.03-LTS-SP3_velinux"
  },
  {
    "url": "https://gitcode.com/kunpengcompute/src-openEuler-ummu/tree/openEuler-24.03-LTS-SP3_velinux"
  },
  {
    "url": "https://gitcode.com/kunpengcompute/src-openEuler-cdma/tree/openEuler-24.03-LTS-SP3_velinux"
  },
  {
    "url": "https://gitcode.com/kunpengcompute/src-openEuler-sysSentry/tree/openEuler-24.03-LTS-SP3_velinux"
  }
]
```

- [ ] **Step 4: Create `src/specdeps/__init__.py`**

Write this exact file:

```python
"""Tools for extracting install dependency topology from RPM spec files."""

__version__ = "0.1.0"
```

- [ ] **Step 5: Run package import smoke check**

Run:

```bash
PYTHONPATH=src python -c "import specdeps; print(specdeps.__version__)"
```

Expected:

```text
0.1.0
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml config/repos.json src/specdeps/__init__.py
git commit -m "chore: bootstrap spec dependency tool"
```

### Task 2: Repository Config Loader

**Files:**
- Create: `src/specdeps/models.py`
- Create: `src/specdeps/repo_config.py`
- Create: `tests/test_repo_config.py`

- [ ] **Step 1: Write failing tests for URL normalization and config loading**

Create `tests/test_repo_config.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from specdeps.repo_config import load_repos, normalize_repo_url


class RepoConfigTests(unittest.TestCase):
    def test_normalize_gitcode_tree_url(self):
        repo = normalize_repo_url(
            "https://gitcode.com/kunpengcompute/src-openEuler-qemu/tree/openEuler-24.03-LTS-SP3_velinux"
        )

        self.assertEqual(repo.name, "src-openEuler-qemu")
        self.assertEqual(repo.branch, "openEuler-24.03-LTS-SP3_velinux")
        self.assertEqual(repo.clone_url, "https://gitcode.com/kunpengcompute/src-openEuler-qemu.git")
        self.assertEqual(
            repo.page_url,
            "https://gitcode.com/kunpengcompute/src-openEuler-qemu/tree/openEuler-24.03-LTS-SP3_velinux",
        )

    def test_load_repos_preserves_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "repos.json"
            config_path.write_text(
                json.dumps(
                    [
                        {
                            "url": "https://gitcode.com/kunpengcompute/src-openEuler-ubutils/tree/openEuler-24.03-LTS-SP3_velinux"
                        },
                        {
                            "url": "https://gitcode.com/kunpengcompute/src-openEuler-ubctl/tree/openEuler-24.03-LTS-SP3_velinux"
                        },
                    ]
                ),
                encoding="utf-8",
            )

            repos = load_repos(config_path)

        self.assertEqual([repo.name for repo in repos], ["src-openEuler-ubutils", "src-openEuler-ubctl"])
        self.assertEqual(repos[1].branch, "openEuler-24.03-LTS-SP3_velinux")

    def test_load_repos_rejects_missing_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "repos.json"
            config_path.write_text(json.dumps([{"name": "broken"}]), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "entry 0 must contain a url"):
                load_repos(config_path)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_repo_config -v
```

Expected:

```text
ModuleNotFoundError: No module named 'specdeps.repo_config'
```

- [ ] **Step 3: Implement shared models**

Create `src/specdeps/models.py`:

```python
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
```

- [ ] **Step 4: Implement repo config loading**

Create `src/specdeps/repo_config.py`:

```python
from __future__ import annotations

import json
import re
from pathlib import Path

from .models import RepoRef


TREE_URL_RE = re.compile(
    r"^(?P<base>https://gitcode\.com/[^/]+/(?P<repo>[^/?#]+))/tree/(?P<branch>[^/?#]+)$"
)


def normalize_repo_url(page_url: str) -> RepoRef:
    match = TREE_URL_RE.match(page_url.strip())
    if not match:
        raise ValueError(f"unsupported GitCode tree URL: {page_url}")

    base = match.group("base")
    repo_name = match.group("repo")
    branch = match.group("branch")
    return RepoRef(
        name=repo_name,
        page_url=page_url.strip(),
        clone_url=f"{base}.git",
        branch=branch,
    )


def load_repos(config_path: str | Path) -> list[RepoRef]:
    path = Path(config_path)
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError(f"{path} must contain a JSON array")

    repos: list[RepoRef] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not entry.get("url"):
            raise ValueError(f"entry {index} must contain a url")
        repos.append(normalize_repo_url(str(entry["url"])))
    return repos
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_repo_config -v
```

Expected:

```text
Ran 3 tests

OK
```

- [ ] **Step 6: Commit**

```bash
git add src/specdeps/models.py src/specdeps/repo_config.py tests/test_repo_config.py
git commit -m "feat: load GitCode repository config"
```

### Task 3: RPM Spec Install Dependency Parser

**Files:**
- Create: `src/specdeps/spec_parser.py`
- Create: `tests/test_spec_parser.py`

- [ ] **Step 1: Write failing parser tests**

Create `tests/test_spec_parser.py`:

```python
import tempfile
import textwrap
import unittest
from pathlib import Path

from specdeps.spec_parser import dependency_names, expand_macros, parse_spec


class SpecParserTests(unittest.TestCase):
    def test_expand_macros_handles_simple_and_optional_forms(self):
        macros = {"name": "ubutils", "version": "1.2.3"}

        self.assertEqual(expand_macros("%{name} = %{version}", macros), "ubutils = 1.2.3")
        self.assertEqual(expand_macros("%{?name}-devel", macros), "ubutils-devel")
        self.assertEqual(expand_macros("%{?_isa}", macros), "")

    def test_dependency_names_ignores_versions_and_boolean_words(self):
        names = dependency_names("ubs-comm >= 1.0, (ubutils if qemu), libvirt%{?_isa}", {})

        self.assertEqual(names, ["ubs-comm", "ubutils", "qemu", "libvirt"])

    def test_parse_spec_collects_packages_provides_and_runtime_requires(self):
        content = """
        %global common_pkg ubs-comm
        Name:           ubs-engine
        Version:        1.0.0
        Release:        1
        Summary:        fixture
        BuildRequires:  gcc
        Requires:       %{common_pkg} >= 1.0
        Requires(post): ubutils

        %package devel
        Summary:        development files
        Requires:       %{name}%{?_isa} = %{version}-%{release}

        %package -n libubsengine
        Summary:        library
        Provides:       ubs-engine-lib = %{version}
        Requires(preun): libvirt >= 9.0
        """

        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "ubs-engine.spec"
            spec_path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")

            info = parse_spec(spec_path, "src-openEuler-ubs-engine")

        self.assertEqual(info.repo, "src-openEuler-ubs-engine")
        self.assertEqual(info.source_name, "ubs-engine")
        self.assertEqual(
            info.packages,
            frozenset({"ubs-engine", "ubs-engine-devel", "libubsengine"}),
        )
        self.assertEqual(info.provides, frozenset({"ubs-engine-lib"}))
        self.assertEqual(info.requires, ("ubs-comm", "ubutils", "ubs-engine", "libvirt"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_spec_parser -v
```

Expected:

```text
ModuleNotFoundError: No module named 'specdeps.spec_parser'
```

- [ ] **Step 3: Implement the spec parser**

Create `src/specdeps/spec_parser.py`:

```python
from __future__ import annotations

import re
import shlex
from pathlib import Path

from .models import SpecInfo


HEADER_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9_()]+)\s*:\s*(?P<value>.*)$")
MACRO_RE = re.compile(r"^%(?:global|define)\s+(?P<key>[A-Za-z0-9_]+)\s+(?P<value>.*)$")
MACRO_REF_RE = re.compile(r"%\{(?P<optional>\?)?(?P<name>[A-Za-z0-9_]+)(?::[^}]*)?\}")
TOKEN_RE = re.compile(r"[A-Za-z_+.-][A-Za-z0-9_+.-]*|>=|<=|=|>|<|\(|\)|,")
VERSION_OPERATORS = {">=", "<=", "=", ">", "<"}
BOOLEAN_WORDS = {"and", "or", "if", "with", "without"}


def expand_macros(value: str, macros: dict[str, str]) -> str:
    expanded = value
    for _ in range(8):
        next_value = MACRO_REF_RE.sub(lambda match: macros.get(match.group("name"), ""), expanded)
        if next_value == expanded:
            return next_value
        expanded = next_value
    return expanded


def dependency_names(value: str, macros: dict[str, str]) -> list[str]:
    expanded = expand_macros(value, macros).replace(",", " ")
    tokens = TOKEN_RE.findall(expanded)
    names: list[str] = []
    skip_version = False

    for token in tokens:
        lower = token.lower()
        if token in {"(", ")", ","}:
            continue
        if token in VERSION_OPERATORS:
            skip_version = True
            continue
        if skip_version:
            skip_version = False
            continue
        if lower in BOOLEAN_WORDS:
            continue
        names.append(token)

    return _dedupe(names)


def parse_spec(spec_path: str | Path, repo_name: str) -> SpecInfo:
    path = Path(spec_path)
    macros: dict[str, str] = {}
    source_name = ""
    packages: set[str] = set()
    provides: list[str] = []
    requires: list[str] = []

    for raw_line in _logical_lines(path.read_text(encoding="utf-8", errors="replace").splitlines()):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        macro_match = MACRO_RE.match(line)
        if macro_match:
            macros[macro_match.group("key")] = expand_macros(macro_match.group("value").strip(), macros)
            continue

        header_match = HEADER_RE.match(line)
        if header_match:
            key = header_match.group("key")
            value = header_match.group("value").strip()
            normalized_key = key.split("(", 1)[0].lower()
            expanded_value = expand_macros(value, macros)

            if normalized_key == "name":
                source_name = expanded_value
                packages.add(source_name)
                macros["name"] = source_name
            elif normalized_key in {"version", "release"}:
                macros[normalized_key] = expanded_value
            elif normalized_key == "requires":
                requires.extend(dependency_names(value, macros))
            elif normalized_key == "provides":
                provides.extend(dependency_names(value, macros))
            continue

        if line.startswith("%package"):
            package_name = _parse_package_name(line, source_name, macros)
            if package_name:
                packages.add(package_name)

    if not source_name:
        raise ValueError(f"{path} does not define Name")

    return SpecInfo(
        repo=repo_name,
        spec_path=str(path),
        source_name=source_name,
        packages=frozenset(_dedupe(sorted(packages))),
        provides=frozenset(_dedupe(sorted(provides))),
        requires=tuple(_dedupe(requires)),
    )


def _logical_lines(lines: list[str]) -> list[str]:
    logical: list[str] = []
    current = ""
    for line in lines:
        if line.rstrip().endswith("\\"):
            current += line.rstrip()[:-1] + " "
            continue
        logical.append(current + line if current else line)
        current = ""
    if current:
        logical.append(current)
    return logical


def _parse_package_name(line: str, source_name: str, macros: dict[str, str]) -> str:
    parts = shlex.split(line)
    if "-n" in parts:
        index = parts.index("-n")
        if index + 1 < len(parts):
            return expand_macros(parts[index + 1], macros)
        return ""

    if len(parts) >= 2 and source_name:
        suffix = expand_macros(parts[1].lstrip("-"), macros)
        return f"{source_name}-{suffix}"

    return source_name


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
```

- [ ] **Step 4: Run parser tests to verify they pass**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_spec_parser -v
```

Expected:

```text
Ran 3 tests

OK
```

- [ ] **Step 5: Commit**

```bash
git add src/specdeps/spec_parser.py tests/test_spec_parser.py
git commit -m "feat: parse install dependencies from rpm specs"
```

### Task 4: Repository Fetcher And Spec Discovery

**Files:**
- Create: `src/specdeps/fetcher.py`
- Create: `tests/test_fetcher.py`

- [ ] **Step 1: Write failing fetcher tests**

Create `tests/test_fetcher.py`:

```python
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from specdeps.fetcher import checkout_repos, find_spec_files
from specdeps.models import RepoRef


class FetcherTests(unittest.TestCase):
    def test_find_spec_files_prefers_root_specs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "nested"
            nested.mkdir()
            (root / "qemu.spec").write_text("Name: qemu\n", encoding="utf-8")
            (nested / "ignored.spec").write_text("Name: ignored\n", encoding="utf-8")

            specs = find_spec_files(root)

        self.assertEqual([path.name for path in specs], ["qemu.spec"])

    def test_find_spec_files_recurses_when_root_has_no_specs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "rpm"
            nested.mkdir()
            (nested / "libvirt.spec").write_text("Name: libvirt\n", encoding="utf-8")

            specs = find_spec_files(root)

        self.assertEqual([path.name for path in specs], ["libvirt.spec"])

    def test_checkout_repos_clones_missing_directory(self):
        repo = RepoRef(
            name="src-openEuler-qemu",
            page_url="https://gitcode.com/kunpengcompute/src-openEuler-qemu/tree/openEuler-24.03-LTS-SP3_velinux",
            clone_url="https://gitcode.com/kunpengcompute/src-openEuler-qemu.git",
            branch="openEuler-24.03-LTS-SP3_velinux",
        )

        with tempfile.TemporaryDirectory() as tmp, patch("specdeps.fetcher.subprocess.run") as run:
            paths = checkout_repos([repo], Path(tmp))

        self.assertEqual(paths["src-openEuler-qemu"], Path(tmp) / "src-openEuler-qemu")
        run.assert_called_once_with(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                "openEuler-24.03-LTS-SP3_velinux",
                "https://gitcode.com/kunpengcompute/src-openEuler-qemu.git",
                str(Path(tmp) / "src-openEuler-qemu"),
            ],
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_fetcher -v
```

Expected:

```text
ModuleNotFoundError: No module named 'specdeps.fetcher'
```

- [ ] **Step 3: Implement fetcher**

Create `src/specdeps/fetcher.py`:

```python
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
```

- [ ] **Step 4: Run fetcher tests to verify they pass**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_fetcher -v
```

Expected:

```text
Ran 3 tests

OK
```

- [ ] **Step 5: Commit**

```bash
git add src/specdeps/fetcher.py tests/test_fetcher.py
git commit -m "feat: fetch repositories and discover spec files"
```

### Task 5: Dependency Graph Builder And Renderers

**Files:**
- Create: `src/specdeps/graph.py`
- Create: `tests/test_graph.py`

- [ ] **Step 1: Write failing graph tests**

Create `tests/test_graph.py`:

```python
import unittest

from specdeps.graph import build_dependency_graph, graph_to_dict, render_dot, render_mermaid
from specdeps.models import SpecInfo


class GraphTests(unittest.TestCase):
    def test_build_dependency_graph_maps_requires_to_repo_packages_and_provides(self):
        specs = [
            SpecInfo(
                repo="src-openEuler-ubs-engine",
                spec_path="ubs-engine.spec",
                source_name="ubs-engine",
                packages=frozenset({"ubs-engine", "libubsengine"}),
                provides=frozenset({"ubs-engine-lib"}),
                requires=("ubs-comm", "ubutils", "glibc"),
            ),
            SpecInfo(
                repo="src-openEuler-ubs-comm",
                spec_path="ubs-comm.spec",
                source_name="ubs-comm",
                packages=frozenset({"ubs-comm"}),
                provides=frozenset({"libubscomm"}),
                requires=("ubutils",),
            ),
            SpecInfo(
                repo="src-openEuler-ubutils",
                spec_path="ubutils.spec",
                source_name="ubutils",
                packages=frozenset({"ubutils"}),
                provides=frozenset(),
                requires=("bash",),
            ),
        ]

        graph = build_dependency_graph(specs)

        self.assertEqual(graph.repos, ("src-openEuler-ubs-comm", "src-openEuler-ubs-engine", "src-openEuler-ubutils"))
        self.assertEqual(
            [(edge.source_repo, edge.target_repo, edge.dependency) for edge in graph.edges],
            [
                ("src-openEuler-ubs-comm", "src-openEuler-ubutils", "ubutils"),
                ("src-openEuler-ubs-engine", "src-openEuler-ubs-comm", "ubs-comm"),
                ("src-openEuler-ubs-engine", "src-openEuler-ubutils", "ubutils"),
            ],
        )
        self.assertEqual(graph.external_requires["src-openEuler-ubs-engine"], ("glibc",))
        self.assertEqual(graph.external_requires["src-openEuler-ubutils"], ("bash",))

    def test_render_mermaid_and_dot(self):
        specs = [
            SpecInfo(
                repo="src-openEuler-qemu",
                spec_path="qemu.spec",
                source_name="qemu",
                packages=frozenset({"qemu"}),
                provides=frozenset(),
                requires=("libvirt",),
            ),
            SpecInfo(
                repo="src-openEuler-libvirt",
                spec_path="libvirt.spec",
                source_name="libvirt",
                packages=frozenset({"libvirt"}),
                provides=frozenset(),
                requires=(),
            ),
        ]
        graph = build_dependency_graph(specs)

        mermaid = render_mermaid(graph)
        dot = render_dot(graph)
        as_dict = graph_to_dict(graph)

        self.assertIn('src_openEuler_qemu["src-openEuler-qemu"]', mermaid)
        self.assertIn("src_openEuler_qemu -->|libvirt| src_openEuler_libvirt", mermaid)
        self.assertIn('"src-openEuler-qemu" -> "src-openEuler-libvirt" [label="libvirt"];', dot)
        self.assertEqual(as_dict["edges"][0]["dependency"], "libvirt")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_graph -v
```

Expected:

```text
ModuleNotFoundError: No module named 'specdeps.graph'
```

- [ ] **Step 3: Implement graph builder and renderers**

Create `src/specdeps/graph.py`:

```python
from __future__ import annotations

import re
from collections import defaultdict

from .models import DependencyEdge, DependencyGraph, SpecInfo


def build_dependency_graph(specs: list[SpecInfo]) -> DependencyGraph:
    repos = tuple(sorted({spec.repo for spec in specs}))
    package_index: dict[str, str] = {}

    for spec in sorted(specs, key=lambda item: item.repo):
        for package_name in sorted(set(spec.packages) | set(spec.provides) | {spec.source_name}):
            package_index.setdefault(package_name, spec.repo)

    edge_keys: set[tuple[str, str, str]] = set()
    external: dict[str, set[str]] = defaultdict(set)

    for spec in specs:
        for requirement in spec.requires:
            target_repo = package_index.get(requirement)
            if target_repo and target_repo != spec.repo:
                edge_keys.add((spec.repo, target_repo, requirement))
            elif not target_repo:
                external[spec.repo].add(requirement)

    edges = tuple(DependencyEdge(*edge) for edge in sorted(edge_keys))
    external_requires = {repo: tuple(sorted(values)) for repo, values in sorted(external.items())}
    return DependencyGraph(repos=repos, edges=edges, external_requires=external_requires)


def render_mermaid(graph: DependencyGraph) -> str:
    lines = ["flowchart LR"]
    for repo in graph.repos:
        lines.append(f'    {_node_id(repo)}["{repo}"]')
    for edge in graph.edges:
        lines.append(
            f"    {_node_id(edge.source_repo)} -->|{edge.dependency}| {_node_id(edge.target_repo)}"
        )
    return "\n".join(lines) + "\n"


def render_dot(graph: DependencyGraph) -> str:
    lines = ["digraph install_dependencies {", "    rankdir=LR;"]
    for repo in graph.repos:
        lines.append(f'    "{repo}";')
    for edge in graph.edges:
        lines.append(f'    "{edge.source_repo}" -> "{edge.target_repo}" [label="{edge.dependency}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def graph_to_dict(graph: DependencyGraph) -> dict[str, object]:
    return {
        "repos": list(graph.repos),
        "edges": [
            {
                "source_repo": edge.source_repo,
                "target_repo": edge.target_repo,
                "dependency": edge.dependency,
            }
            for edge in graph.edges
        ],
        "external_requires": {repo: list(values) for repo, values in graph.external_requires.items()},
    }


def _node_id(repo: str) -> str:
    node = re.sub(r"[^A-Za-z0-9_]", "_", repo)
    if node and node[0].isdigit():
        return f"repo_{node}"
    return node
```

- [ ] **Step 4: Run graph tests to verify they pass**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_graph -v
```

Expected:

```text
Ran 2 tests

OK
```

- [ ] **Step 5: Commit**

```bash
git add src/specdeps/graph.py tests/test_graph.py
git commit -m "feat: build dependency topology graph"
```

### Task 6: Markdown Report Renderer

**Files:**
- Create: `src/specdeps/report.py`
- Create: `tests/test_report.py`

- [ ] **Step 1: Write failing report test**

Create `tests/test_report.py`:

```python
import unittest

from specdeps.graph import build_dependency_graph, render_mermaid
from specdeps.models import SpecInfo
from specdeps.report import render_report


class ReportTests(unittest.TestCase):
    def test_render_report_includes_mermaid_edges_and_external_requires(self):
        specs = [
            SpecInfo(
                repo="src-openEuler-qemu",
                spec_path="work/repos/src-openEuler-qemu/qemu.spec",
                source_name="qemu",
                packages=frozenset({"qemu"}),
                provides=frozenset(),
                requires=("libvirt", "glibc"),
            ),
            SpecInfo(
                repo="src-openEuler-libvirt",
                spec_path="work/repos/src-openEuler-libvirt/libvirt.spec",
                source_name="libvirt",
                packages=frozenset({"libvirt"}),
                provides=frozenset(),
                requires=(),
            ),
        ]
        graph = build_dependency_graph(specs)
        report = render_report(graph, specs, render_mermaid(graph))

        self.assertIn("# RPM Spec Install Dependency Topology", report)
        self.assertIn("| src-openEuler-qemu | src-openEuler-libvirt | libvirt |", report)
        self.assertIn("- `src-openEuler-qemu`: `glibc`", report)
        self.assertIn("```mermaid", report)
        self.assertIn("src_openEuler_qemu -->|libvirt| src_openEuler_libvirt", report)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_report -v
```

Expected:

```text
ModuleNotFoundError: No module named 'specdeps.report'
```

- [ ] **Step 3: Implement report renderer**

Create `src/specdeps/report.py`:

```python
from __future__ import annotations

from .models import DependencyGraph, SpecInfo


def render_report(graph: DependencyGraph, specs: list[SpecInfo], mermaid: str) -> str:
    lines: list[str] = [
        "# RPM Spec Install Dependency Topology",
        "",
        "## Summary",
        "",
        f"- Repositories: {len(graph.repos)}",
        f"- Internal dependency edges: {len(graph.edges)}",
        f"- Repositories with external install requirements: {len(graph.external_requires)}",
        "",
        "## Topology",
        "",
        "```mermaid",
        mermaid.rstrip(),
        "```",
        "",
        "## Internal Edges",
        "",
        "| Source Repository | Target Repository | Required Package |",
        "| --- | --- | --- |",
    ]

    if graph.edges:
        for edge in graph.edges:
            lines.append(f"| {edge.source_repo} | {edge.target_repo} | {edge.dependency} |")
    else:
        lines.append("| No internal dependency edges found |  |  |")

    lines.extend(["", "## Parsed Specs", "", "| Repository | Spec Path | Packages | Provides |", "| --- | --- | --- | --- |"])
    for spec in sorted(specs, key=lambda item: item.repo):
        packages = ", ".join(sorted(spec.packages))
        provides = ", ".join(sorted(spec.provides)) if spec.provides else "-"
        lines.append(f"| {spec.repo} | {spec.spec_path} | {packages} | {provides} |")

    lines.extend(["", "## External Install Requirements", ""])
    if graph.external_requires:
        for repo, requirements in graph.external_requires.items():
            formatted = ", ".join(f"`{requirement}`" for requirement in requirements)
            lines.append(f"- `{repo}`: {formatted}")
    else:
        lines.append("- None")

    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run report tests to verify they pass**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_report -v
```

Expected:

```text
Ran 1 test

OK
```

- [ ] **Step 5: Commit**

```bash
git add src/specdeps/report.py tests/test_report.py
git commit -m "feat: render dependency topology report"
```

### Task 7: CLI Orchestration And Offline Integration Test

**Files:**
- Create: `src/specdeps/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI integration test**

Create `tests/test_cli.py`:

```python
import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from specdeps.cli import main


class CliTests(unittest.TestCase):
    def test_cli_generates_outputs_from_existing_checkouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "repos.json"
            checkout_dir = root / "repos"
            out_dir = root / "out"
            qemu_repo = checkout_dir / "src-openEuler-qemu"
            libvirt_repo = checkout_dir / "src-openEuler-libvirt"
            qemu_repo.mkdir(parents=True)
            libvirt_repo.mkdir(parents=True)

            config_path.write_text(
                json.dumps(
                    [
                        {
                            "url": "https://gitcode.com/kunpengcompute/src-openEuler-qemu/tree/openEuler-24.03-LTS-SP3_velinux"
                        },
                        {
                            "url": "https://gitcode.com/kunpengcompute/src-openEuler-libvirt/tree/openEuler-24.03-LTS-SP3_velinux"
                        },
                    ]
                ),
                encoding="utf-8",
            )
            (qemu_repo / "qemu.spec").write_text(
                textwrap.dedent(
                    """
                    Name: qemu
                    Version: 1
                    Release: 1
                    Requires: libvirt >= 9.0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (libvirt_repo / "libvirt.spec").write_text(
                textwrap.dedent(
                    """
                    Name: libvirt
                    Version: 1
                    Release: 1
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            exit_code = main(
                [
                    "--config",
                    str(config_path),
                    "--checkout-dir",
                    str(checkout_dir),
                    "--out-dir",
                    str(out_dir),
                    "--skip-fetch",
                ]
            )

            dependencies = json.loads((out_dir / "dependencies.json").read_text(encoding="utf-8"))
            mermaid = (out_dir / "dependency-topology.mmd").read_text(encoding="utf-8")
            dot = (out_dir / "dependency-topology.dot").read_text(encoding="utf-8")
            report = (out_dir / "dependency-report.md").read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(dependencies["edges"][0]["source_repo"], "src-openEuler-qemu")
        self.assertEqual(dependencies["edges"][0]["target_repo"], "src-openEuler-libvirt")
        self.assertIn("src_openEuler_qemu -->|libvirt| src_openEuler_libvirt", mermaid)
        self.assertIn('"src-openEuler-qemu" -> "src-openEuler-libvirt" [label="libvirt"];', dot)
        self.assertIn("| src-openEuler-qemu | src-openEuler-libvirt | libvirt |", report)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_cli -v
```

Expected:

```text
ModuleNotFoundError: No module named 'specdeps.cli'
```

- [ ] **Step 3: Implement CLI**

Create `src/specdeps/cli.py`:

```python
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .fetcher import checkout_repos, find_spec_files
from .graph import build_dependency_graph, graph_to_dict, render_dot, render_mermaid
from .models import SpecInfo
from .repo_config import load_repos
from .report import render_report
from .spec_parser import parse_spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract install dependency topology from RPM spec repositories")
    parser.add_argument("--config", default="config/repos.json", help="Path to repository config JSON")
    parser.add_argument("--checkout-dir", default="work/repos", help="Directory where repositories are cloned")
    parser.add_argument("--out-dir", default="out", help="Directory where graph outputs are written")
    parser.add_argument("--skip-fetch", action="store_true", help="Use existing checkout directories")
    args = parser.parse_args(argv)

    repos = load_repos(args.config)
    checkout_root = Path(args.checkout_dir)
    repo_paths = (
        {repo.name: checkout_root / repo.name for repo in repos}
        if args.skip_fetch
        else checkout_repos(repos, checkout_root)
    )

    specs: list[SpecInfo] = []
    for repo in repos:
        spec_files = find_spec_files(repo_paths[repo.name])
        if not spec_files:
            raise FileNotFoundError(f"no spec files found for {repo.name} in {repo_paths[repo.name]}")
        for spec_path in spec_files:
            specs.append(parse_spec(spec_path, repo.name))

    graph = build_dependency_graph(specs)
    mermaid = render_mermaid(graph)
    dot = render_dot(graph)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    dependency_payload = graph_to_dict(graph)
    dependency_payload["specs"] = [
        {
            "repo": spec.repo,
            "spec_path": spec.spec_path,
            "source_name": spec.source_name,
            "packages": sorted(spec.packages),
            "provides": sorted(spec.provides),
            "requires": list(spec.requires),
        }
        for spec in sorted(specs, key=lambda item: (item.repo, item.spec_path))
    ]

    (out_dir / "dependencies.json").write_text(
        json.dumps(dependency_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "dependency-topology.mmd").write_text(mermaid, encoding="utf-8")
    (out_dir / "dependency-topology.dot").write_text(dot, encoding="utf-8")
    (out_dir / "dependency-report.md").write_text(render_report(graph, specs, mermaid), encoding="utf-8")

    print(f"Wrote {out_dir / 'dependencies.json'}")
    print(f"Wrote {out_dir / 'dependency-topology.mmd'}")
    print(f"Wrote {out_dir / 'dependency-topology.dot'}")
    print(f"Wrote {out_dir / 'dependency-report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run CLI integration test to verify it passes**

Run:

```bash
PYTHONPATH=src python -m unittest tests.test_cli -v
```

Expected:

```text
Ran 1 test

OK
```

- [ ] **Step 5: Run full unit test suite**

Run:

```bash
PYTHONPATH=src python -m unittest discover -v
```

Expected:

```text
Ran 13 tests

OK
```

- [ ] **Step 6: Commit**

```bash
git add src/specdeps/cli.py tests/test_cli.py
git commit -m "feat: generate dependency topology outputs"
```

### Task 8: Generate The Real Topology From GitCode Specs

**Files:**
- Read: `config/repos.json`
- Read: remote GitCode repositories listed in `config/repos.json`
- Generate: `work/repos/*`
- Generate: `out/dependencies.json`
- Generate: `out/dependency-topology.mmd`
- Generate: `out/dependency-topology.dot`
- Generate: `out/dependency-report.md`

- [ ] **Step 1: Run the complete tool against the listed repositories**

Run:

```bash
PYTHONPATH=src python -m specdeps.cli --config config/repos.json --checkout-dir work/repos --out-dir out
```

Expected:

```text
Wrote out/dependencies.json
Wrote out/dependency-topology.mmd
Wrote out/dependency-topology.dot
Wrote out/dependency-report.md
```

- [ ] **Step 2: Inspect generated dependency edge count**

Run:

```bash
python -c "import json; data=json.load(open('out/dependencies.json', encoding='utf-8')); print(len(data['repos']), len(data['edges']))"
```

Expected:

```text
14 <edge-count>
```

The `<edge-count>` value must be a non-negative integer. If it is `0`, inspect `out/dependency-report.md` and confirm whether the listed repositories genuinely have no install dependencies on one another.

- [ ] **Step 3: Render a PNG when Graphviz is installed**

Run:

```bash
dot -Tpng out/dependency-topology.dot -o out/dependency-topology.png
```

Expected:

```text
```

Then confirm the image exists:

```bash
test -s out/dependency-topology.png && echo "png ok"
```

Expected:

```text
png ok
```

- [ ] **Step 4: Verify all outputs are present**

Run:

```bash
ls -1 out/dependencies.json out/dependency-report.md out/dependency-topology.dot out/dependency-topology.mmd
```

Expected:

```text
out/dependencies.json
out/dependency-report.md
out/dependency-topology.dot
out/dependency-topology.mmd
```

- [ ] **Step 5: Commit generated source changes and report artifacts**

```bash
git add config/repos.json pyproject.toml src tests out/dependencies.json out/dependency-report.md out/dependency-topology.dot out/dependency-topology.mmd
git commit -m "docs: add rpm install dependency topology"
```

## Self-Review

**Spec coverage:** The plan covers fetching all 14 GitCode repositories, locating `.spec` files, extracting install dependencies from `Requires*` fields, mapping dependencies to packages/provides from the same repository set, and generating topology outputs in Mermaid, DOT, JSON, and Markdown.

**Scope check:** The task is one cohesive pipeline, so it does not need separate subsystem plans.

**Placeholder scan:** The plan contains concrete file paths, exact repository URLs, exact commands, expected outputs, and full test/implementation code for each created module.

**Type consistency:** `RepoRef`, `SpecInfo`, `DependencyEdge`, and `DependencyGraph` are defined once in `src/specdeps/models.py` and used consistently by the parser, fetcher, graph builder, report renderer, and CLI.
