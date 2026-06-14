#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON:-python3}"
VENV_DIR="$ROOT_DIR/.venv"

echo "Creating virtual environment at $VENV_DIR"
"$PYTHON_BIN" -m venv "$VENV_DIR"

echo "Installing specdeps in editable mode"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -e .

if ! command -v rpm >/dev/null 2>&1; then
  echo "Note: rpm command not found. RPM metadata parsing requires rpm on the target machine."
fi

if ! command -v dpkg-deb >/dev/null 2>&1; then
  echo "Note: dpkg-deb command not found. DEB metadata parsing requires dpkg-deb on the target machine."
fi

cat <<EOF

Install complete.

Beginner workflow:
1. cp reinstall.example.txt reinstall.txt
2. $VENV_DIR/bin/specdeps-txt-to-json --input reinstall.txt --out config/reinstall-input.json
3. $VENV_DIR/bin/specdeps-reinstall-json --input config/reinstall-input.json --out out/reinstall.json

ISO workflow:
1. cp reinstall.iso.example.txt reinstall.txt
2. $VENV_DIR/bin/specdeps-iso-reinstall --input reinstall.txt --source /mnt/iso --out out/reinstall.json
3. Review the dry-run output, then rerun with --execute when ready.

Apply an existing reinstall JSON:
$VENV_DIR/bin/specdeps-apply-reinstall --input out/reinstall.json

This script installs the tool only. It does not run remove/install package commands.
EOF
