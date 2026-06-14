from __future__ import annotations

import re
import shlex
from pathlib import Path

from .models import SpecInfo


HEADER_RE = re.compile(r"^(?P<key>[A-Za-z][A-Za-z0-9_()]+)\s*:\s*(?P<value>.*)$")
MACRO_RE = re.compile(r"^%(?:global|define)\s+(?P<key>[A-Za-z0-9_]+)\s+(?P<value>.*)$")
MACRO_REF_RE = re.compile(r"%\{(?P<optional>\?)?(?P<name>[A-Za-z0-9_]+)(?::[^}]*)?\}")
FILE_REQUIRE_RE = re.compile(r"(?<!\S)/(?:[A-Za-z0-9_.+-]+/)*([A-Za-z0-9_.+-]+)")
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
    expanded = FILE_REQUIRE_RE.sub(lambda match: match.group(1), expand_macros(value, macros))
    expanded = expanded.replace(",", " ")
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
                if _looks_like_capability(value, macros):
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


def _looks_like_capability(value: str, macros: dict[str, str]) -> bool:
    expanded = expand_macros(value, macros).strip()
    first_part = expanded.split(",", 1)[0].strip()
    first_token = first_part.split(None, 1)[0] if first_part else ""
    return bool(first_token) and (
        len(first_part.split()) == 1
        or any(operator in first_part for operator in VERSION_OPERATORS)
        or first_token.endswith(")")
    )


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
