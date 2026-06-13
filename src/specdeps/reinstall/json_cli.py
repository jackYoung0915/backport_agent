from __future__ import annotations

import argparse
import json
from pathlib import Path

from .package_metadata import load_package_metadata
from .json_plan import build_reinstall_json, discover_upgrade_artifacts, load_reinstall_input


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate reinstall JSON from package names and upgrade package paths")
    parser.add_argument("--input", default="config/reinstall-input.json", help="Path to reinstall input JSON")
    parser.add_argument("--out", default="out/reinstall.json", help="Path to generated reinstall JSON")
    args = parser.parse_args(argv)

    try:
        config = load_reinstall_input(args.input)
        package_format, artifacts = discover_upgrade_artifacts(config.upgrade_paths)
        metadata = [load_package_metadata(artifact, package_format) for artifact in artifacts]
        payload = build_reinstall_json(config.upgrade_packages, metadata, package_format)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
