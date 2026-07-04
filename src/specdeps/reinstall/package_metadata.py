from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PackageMetadata:
    name: str
    artifact: Path
    provides: tuple[str, ...]
    requires: tuple[str, ...]
    epoch: str = ""
    version: str = ""
    release: str = ""
    arch: str = ""


def load_package_metadata(path: str | Path, package_format: str) -> PackageMetadata:
    artifact = Path(path)
    if package_format == "rpm":
        return _load_rpm_metadata(artifact)
    if package_format == "deb":
        return _load_deb_metadata(artifact)
    raise ValueError("package format must be rpm or deb")


def _load_rpm_metadata(path: Path) -> PackageMetadata:
    query_format = (
        "NAME\\n%{NAME}\\n"
        "EPOCH\\n%{EPOCH}\\n"
        "VERSION\\n%{VERSION}\\n"
        "RELEASE\\n%{RELEASE}\\n"
        "ARCH\\n%{ARCH}\\n"
        "PROVIDES\\n[%{PROVIDENAME}\\n]"
        "REQUIRES\\n[%{REQUIRENAME}\\n]"
    )
    try:
        result = subprocess.run(
            ["rpm", "-qp", "--queryformat", query_format, str(path)],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError("rpm command not found; install rpm to inspect RPM package metadata") from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"failed to read RPM metadata from {path}") from error

    sections = _rpm_sections(result.stdout)
    names = sections.get("NAME", [])
    if not names:
        raise ValueError(f"{path} did not report a package name")
    name = names[0]
    provides = _dedupe(_normalize_dependency_name(value) for value in sections.get("PROVIDES", []))
    requires = _dedupe(
        normalized
        for value in sections.get("REQUIRES", [])
        for normalized in [_normalize_dependency_name(value)]
        if normalized and normalized != name and not normalized.startswith("rpmlib(")
    )
    return PackageMetadata(
        name=name,
        artifact=path,
        provides=tuple(provides or [name]),
        requires=tuple(requires),
        epoch=_metadata_value(sections, "EPOCH"),
        version=_metadata_value(sections, "VERSION"),
        release=_metadata_value(sections, "RELEASE"),
        arch=_metadata_value(sections, "ARCH"),
    )


def _load_deb_metadata(path: Path) -> PackageMetadata:
    try:
        result = subprocess.run(
            ["dpkg-deb", "-f", str(path), "Package", "Provides", "Depends", "Pre-Depends", "Version", "Architecture"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError("dpkg-deb command not found; install dpkg-deb to inspect DEB package metadata") from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"failed to read DEB metadata from {path}") from error

    # When more than one field is requested, "dpkg-deb -f" emits a control
    # stanza ("Field: value") and omits fields that are absent. Parse by field
    # name rather than by line position so missing/reordered fields do not shift
    # the label text (e.g. "Package: ...") into the value.
    control = _parse_deb_control(result.stdout)
    name = control.get("package", "").strip()
    if not name:
        raise ValueError(f"{path} did not report a package name")
    provides = _dedupe([name] + _parse_deb_dependency_field(control.get("provides", "")))
    requires = _dedupe(
        _parse_deb_dependency_field(control.get("depends", ""))
        + _parse_deb_dependency_field(control.get("pre-depends", ""))
    )
    return PackageMetadata(
        name=name,
        artifact=path,
        provides=tuple(provides),
        requires=tuple(requires),
        version=control.get("version", "").strip(),
        arch=control.get("architecture", "").strip(),
    )


def _parse_deb_control(output: str) -> dict[str, str]:
    """Parse a Debian control stanza into a lowercased field -> value map.

    Handles folded continuation lines (lines beginning with whitespace belong to
    the preceding field). Field names are matched case-insensitively.
    """
    fields: dict[str, str] = {}
    current: str | None = None
    for line in output.splitlines():
        if not line.strip():
            continue
        if line[0] in " \t" and current is not None:
            fields[current] = (fields[current] + " " + line.strip()).strip()
            continue
        key, separator, value = line.partition(":")
        if not separator:
            continue
        current = key.strip().lower()
        fields[current] = value.strip()
    return fields


def _metadata_value(sections: dict[str, list[str]], key: str) -> str:
    values = sections.get(key, [])
    value = values[0].strip() if values else ""
    return "" if value == "(none)" else value


def _parse_deb_dependency_field(value: str) -> list[str]:
    names: list[str] = []
    for group in value.split(","):
        for alternative in group.split("|"):
            name = re.sub(r"\s*\([^)]*\)", "", alternative).strip()
            if name:
                names.append(name)
    return names


def _normalize_dependency_name(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return ""
    match = re.search(r"/(?:[A-Za-z0-9_.+-]+/)*([A-Za-z0-9_.+-]+)$", stripped)
    if match:
        return match.group(1)
    return re.split(r"\s*(?:>=|<=|=|>|<)\s*", stripped, maxsplit=1)[0].strip()


def _non_empty_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _rpm_sections(output: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {
        "NAME": [],
        "EPOCH": [],
        "VERSION": [],
        "RELEASE": [],
        "ARCH": [],
        "PROVIDES": [],
        "REQUIRES": [],
    }
    current = ""
    for line in _non_empty_lines(output):
        if line in sections:
            current = line
            continue
        if current:
            sections[current].append(line)
    return sections


def _dedupe(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
