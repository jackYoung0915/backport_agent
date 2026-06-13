from __future__ import annotations

import argparse
import json
from pathlib import Path

from .apply_cli import run_reinstall_actions
from .iso_source import build_iso_reinstall_json
from .txt_input import parse_package_only_txt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate reinstall JSON from package names and a mounted ISO source")
    parser.add_argument("--input", default="reinstall.txt", help="Path to package-only reinstall TXT")
    parser.add_argument("--source", action="append", default=[], help="Mounted ISO source path; defaults to /mnt/iso")
    parser.add_argument("--out", default="out/reinstall.json", help="Path to generated reinstall JSON")
    parser.add_argument("--execute", action="store_true", help="Execute commands after generating JSON; default is dry-run")
    args = parser.parse_args(argv)

    try:
        input_path = Path(args.input)
        if not input_path.exists():
            raise FileNotFoundError(f"{input_path} does not exist")
        selected_packages = tuple(parse_package_only_txt(input_path.read_text(encoding="utf-8")))
        sources = tuple(Path(path) for path in (args.source or ["/mnt/iso"]))
        payload = build_iso_reinstall_json(selected_packages, sources)
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return run_reinstall_actions(payload, execute=args.execute)
    except (FileNotFoundError, NotADirectoryError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
