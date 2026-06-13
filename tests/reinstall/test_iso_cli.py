import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class IsoReinstallCliTests(unittest.TestCase):
    def test_cli_generates_json_and_dry_runs_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "reinstall.txt"
            source = root / "iso"
            out_path = root / "out" / "reinstall.json"
            source.mkdir()
            input_path.write_text("packages:\napp\n", encoding="utf-8")
            payload = {
                "actions": [
                    {"phase": "install", "package": "app", "command": ["sudo", "dnf", "install", "-y", str(source / "app.rpm")]}
                ]
            }

            with patch("specdeps.reinstall.iso_cli.build_iso_reinstall_json", return_value=payload) as build, patch(
                "specdeps.reinstall.iso_cli.run_reinstall_actions",
                return_value=0,
            ) as apply:
                from specdeps.reinstall.iso_cli import main

                exit_code = main(["--input", str(input_path), "--source", str(source), "--out", str(out_path)])

            written = json.loads(out_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(written, payload)
        build.assert_called_once_with(("app",), (source,))
        apply.assert_called_once_with(payload, execute=False)


if __name__ == "__main__":
    unittest.main()
