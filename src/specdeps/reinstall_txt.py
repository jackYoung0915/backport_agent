from __future__ import annotations


PACKAGE_HEADERS = {"升级包名", "包名", "packages", "package", "upgrade_packages", "upgrade packages"}
PATH_HEADERS = {"升级包路径", "包路径", "路径", "paths", "path", "upgrade_paths", "upgrade paths"}


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
            packages.append(line)
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


def _header_name(line: str) -> str:
    for suffix in (":", "："):
        if line.endswith(suffix):
            return line[: -len(suffix)].strip()
    return ""


def _normalize_header(header: str) -> str:
    return " ".join(header.strip().lower().split())


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
