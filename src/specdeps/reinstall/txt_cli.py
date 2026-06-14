from __future__ import annotations

import argparse
import json
from pathlib import Path

from .txt_input import parse_reinstall_txt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert beginner-friendly reinstall TXT input to reinstall JSON input")
    parser.add_argument("--input", default="reinstall.txt", help="Path to beginner-friendly reinstall TXT")
    parser.add_argument("--out", default="config/reinstall-input.json", help="Path to generated reinstall input JSON")
    args = parser.parse_args(argv)

    try:
        input_path = Path(args.input)
        if not input_path.exists():
            raise FileNotFoundError(f"{input_path} does not exist")
        payload = parse_reinstall_txt(input_path.read_text(encoding="utf-8"))
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (FileNotFoundError, ValueError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
