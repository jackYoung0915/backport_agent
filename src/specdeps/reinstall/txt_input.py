from __future__ import annotations

import re


PACKAGE_HEADERS = {"升级包名", "包名", "packages", "package", "upgrade_packages", "upgrade packages"}
PATH_HEADERS = {"升级包路径", "包路径", "路径", "paths", "path", "upgrade_paths", "upgrade paths"}

# A Debian/RPM control field line looks like "Field: value". Field names follow
# Debian syntax (start with a letter/digit, then letters/digits/hyphens). We
# require whitespace after an ASCII colon so that architecture-qualified names
# such as "libfoo:amd64" are preserved as package tokens rather than parsed as
# a field. A full-width colon (common in Chinese-locale pastes) is always
# treated as a field separator.
_CONTROL_FIELD_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]*):\s+(.*)$")


def parse_reinstall_txt(text: str) -> dict[str, list[str]]:
    packages: list[str] = []
    paths: list[str] = []
    current: str | None = None
    saw_package_section = False
    saw_path_section = False

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        header = _header_name(line)
        if header:
            normalized = _normalize_header(header)
            if normalized in PACKAGE_HEADERS:
                current = "packages"
                saw_package_section = True
                continue
            if normalized in PATH_HEADERS:
                current = "paths"
                saw_path_section = True
                continue
            raise ValueError(f"unknown section header on line {line_number}: {header}")

        if current == "packages":
            package = _package_line_value(line)
            if package:
                packages.append(package)
        elif current == "paths":
            paths.append(line)
        else:
            raise ValueError(f"value before section header on line {line_number}: {line}")

    if not saw_package_section:
        raise ValueError("missing package section; add '升级包名:' or 'packages:'")
    if not saw_path_section:
        raise ValueError("missing path section; add '升级包路径:' or 'paths:'")

    packages = _dedupe(packages)
    paths = _dedupe(paths)
    if not packages:
        raise ValueError("package section is empty")
    if not paths:
        raise ValueError("path section is empty")
    return {"upgrade_packages": packages, "upgrade_paths": paths}


def parse_package_only_txt(text: str) -> list[str]:
    packages: list[str] = []
    current: str | None = None
    saw_package_section = False

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        header = _header_name(line)
        if header:
            normalized = _normalize_header(header)
            if normalized in PACKAGE_HEADERS:
                current = "packages"
                saw_package_section = True
                continue
            if normalized in PATH_HEADERS:
                raise ValueError(f"unknown section header on line {line_number}: {header}")
            raise ValueError(f"unknown section header on line {line_number}: {header}")

        if current == "packages":
            package = _package_line_value(line)
            if package:
                packages.append(package)
        else:
            raise ValueError(f"value before section header on line {line_number}: {line}")

    if not saw_package_section:
        raise ValueError("missing package section; add '升级包名:' or 'packages:'")

    packages = _dedupe(packages)
    if not packages:
        raise ValueError("package section is empty")
    return packages


def _header_name(line: str) -> str:
    for suffix in (":", "："):
        if line.endswith(suffix):
            return line[: -len(suffix)].strip()
    return ""


def _normalize_header(header: str) -> str:
    return " ".join(header.strip().lower().split())


def _package_line_value(line: str) -> str:
    """Return the package name for a line inside a package section.

    Bare tokens (``app``, ``libfoo:amd64``) are returned unchanged. Debian/RPM
    control fields are recognised generically: only ``Package:`` contributes a
    name; every other field (``Version:``, ``Architecture:``, ``Multi-Arch:``,
    ...) is ignored so pasting a full control stanza does not leak field lines
    into the package list.
    """
    field, value = _split_field(line)
    if field is None:
        return line
    if _normalize_header(field) == "package":
        return value
    return ""


def _split_field(line: str) -> tuple[str | None, str]:
    """Split a control field line into ``(field, value)``.

    Returns ``(None, "")`` when the line is not a ``Field: value`` control line
    (i.e. it is a bare package token).
    """
    full_width_index = line.find("：")
    if full_width_index != -1:
        field = line[:full_width_index].strip()
        value = line[full_width_index + 1:].strip()
        if field:
            return field, value
        return None, ""

    match = _CONTROL_FIELD_RE.match(line)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return None, ""


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
