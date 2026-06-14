from __future__ import annotations

import argparse
import json
from pathlib import Path

from .apply_cli import run_reinstall_actions
from .source_plan import build_source_reinstall_json
from .txt_input import parse_package_only_txt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate reinstall JSON that installs packages from configured repositories")
    parser.add_argument("--input", default="reinstall.txt", help="Path to package-only reinstall TXT")
    parser.add_argument("--manager", choices=["dnf", "apt"], default="dnf", help="Package manager command style")
    parser.add_argument("--out", default="out/reinstall.json", help="Path to generated reinstall JSON")
    parser.add_argument("--no-sudo", action="store_true", help="Do not prefix package commands with sudo")
    parser.add_argument("--execute", action="store_true", help="Execute commands after generating JSON; default is dry-run")
    args = parser.parse_args(argv)

    try:
        input_path = Path(args.input)
        if not input_path.exists():
            raise FileNotFoundError(f"{input_path} does not exist")
        selected_packages = tuple(parse_package_only_txt(input_path.read_text(encoding="utf-8")))
        payload = build_source_reinstall_json(
            selected_packages,
            args.manager,
            sudo=not args.no_sudo,
        )
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return run_reinstall_actions(payload, execute=args.execute)
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
