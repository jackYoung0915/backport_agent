import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ApplyReinstallCliTests(unittest.TestCase):
    def test_dry_run_prints_commands_without_executing(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "reinstall.json"
            plan.write_text(
                json.dumps({"actions": [{"phase": "install", "package": "app", "command": ["sudo", "dnf", "install", "-y", "/mnt/iso/app.rpm"]}]}),
                encoding="utf-8",
            )

            with patch("specdeps.reinstall.apply_cli.subprocess.run") as run, patch("builtins.print") as print_:
                from specdeps.reinstall.apply_cli import main

                exit_code = main(["--input", str(plan)])

        self.assertEqual(exit_code, 0)
        run.assert_not_called()
        printed = "\n".join(" ".join(str(part) for part in call.args) for call in print_.call_args_list)
        self.assertIn("DRY-RUN", printed)
        self.assertIn("sudo dnf install -y /mnt/iso/app.rpm", printed)

    def test_execute_runs_commands_in_order_and_stops_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "reinstall.json"
            plan.write_text(
                json.dumps(
                    {
                        "actions": [
                            {"phase": "uninstall", "package": "app", "command": ["sudo", "dnf", "remove", "-y", "app"]},
                            {"phase": "install", "package": "app", "command": ["sudo", "dnf", "install", "-y", "/mnt/iso/app.rpm"]},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            failure = subprocess.CalledProcessError(1, ["sudo", "dnf", "remove", "-y", "app"])
            with patch("specdeps.reinstall.apply_cli.subprocess.run", side_effect=failure) as run, patch("sys.stderr", io.StringIO()) as stderr:
                from specdeps.reinstall.apply_cli import main

                with self.assertRaises(SystemExit) as error:
                    main(["--input", str(plan), "--execute"])

        self.assertEqual(error.exception.code, 2)
        self.assertEqual(run.call_count, 1)
        self.assertIn("command failed", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_execute_reports_missing_command_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "reinstall.json"
            plan.write_text(
                json.dumps({"actions": [{"phase": "install", "package": "app", "command": ["missing-command", "app"]}]}),
                encoding="utf-8",
            )

            with patch("specdeps.reinstall.apply_cli.subprocess.run", side_effect=FileNotFoundError("missing-command")), patch(
                "sys.stderr",
                io.StringIO(),
            ) as stderr:
                from specdeps.reinstall.apply_cli import main

                with self.assertRaises(SystemExit) as error:
                    main(["--input", str(plan), "--execute"])

        self.assertEqual(error.exception.code, 2)
        self.assertIn("command failed", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_rejects_invalid_command_shape_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "reinstall.json"
            plan.write_text(json.dumps({"actions": [{"phase": "install", "package": "app", "command": "dnf install app"}]}), encoding="utf-8")

            with patch("sys.stderr", io.StringIO()) as stderr:
                from specdeps.reinstall.apply_cli import main

                with self.assertRaises(SystemExit) as error:
                    main(["--input", str(plan)])

        self.assertEqual(error.exception.code, 2)
        self.assertIn("action 0 command must be a non-empty list of strings", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
