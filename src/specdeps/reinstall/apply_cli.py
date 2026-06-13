from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run or execute commands from reinstall JSON")
    parser.add_argument("--input", default="out/reinstall.json", help="Path to reinstall JSON")
    parser.add_argument("--execute", action="store_true", help="Execute commands; default is dry-run")
    args = parser.parse_args(argv)

    try:
        payload = load_reinstall_actions(args.input)
        return run_reinstall_actions(payload, execute=args.execute)
    except (FileNotFoundError, json.JSONDecodeError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    return 0


def load_reinstall_actions(path: str | Path) -> dict[str, Any]:
    input_path = Path(path)
    if not input_path.exists():
        raise FileNotFoundError(f"{input_path} does not exist")
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{input_path} must contain a JSON object")
    _validated_actions(payload)
    return payload


def run_reinstall_actions(payload: dict[str, Any], *, execute: bool) -> int:
    actions = _validated_actions(payload)
    print("EXECUTE" if execute else "DRY-RUN")
    for index, action in enumerate(actions, start=1):
        print(f"{index}. [{action.get('phase', '')}] {action.get('package', '')}: {_format_command(action['command'])}")

    if execute:
        for action in actions:
            try:
                subprocess.run(action["command"], check=True)
            except subprocess.CalledProcessError as error:
                raise RuntimeError(f"command failed: {_format_command(tuple(str(part) for part in error.cmd))}") from error
            except OSError as error:
                raise RuntimeError(f"command failed: {_format_command(action['command'])}: {error}") from error
    return 0


def _validated_actions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    actions = payload.get("actions")
    if not isinstance(actions, list):
        raise ValueError("actions must be a list")
    for index, action in enumerate(actions):
        if not isinstance(action, dict):
            raise ValueError(f"action {index} must be an object")
        command = action.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
            raise ValueError(f"action {index} command must be a non-empty list of strings")
    return actions


def _format_command(command: tuple[str, ...] | list[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


if __name__ == "__main__":
    raise SystemExit(main())
