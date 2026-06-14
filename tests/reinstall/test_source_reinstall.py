import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from specdeps.reinstall.source_plan import build_source_reinstall_json


class SourceReinstallPlanTests(unittest.TestCase):
    def test_builds_dnf_commands_from_configured_repo(self):
        payload = build_source_reinstall_json(("gcc", "libgcc"), "dnf")

        self.assertEqual(payload["install_source"], "configured_repo")
        self.assertEqual(payload["manager"], "dnf")
        self.assertEqual(payload["selected_packages"], ["gcc", "libgcc"])
        self.assertEqual(payload["order"], {"uninstall": ["gcc", "libgcc"], "install": ["gcc", "libgcc"]})
        self.assertEqual(
            [(action["phase"], action["package"], action["command"]) for action in payload["actions"]],
            [
                ("uninstall", "gcc", ["sudo", "dnf", "remove", "-y", "gcc"]),
                ("uninstall", "libgcc", ["sudo", "dnf", "remove", "-y", "libgcc"]),
                ("install", "gcc", ["sudo", "dnf", "install", "-y", "gcc"]),
                ("install", "libgcc", ["sudo", "dnf", "install", "-y", "libgcc"]),
            ],
        )

    def test_builds_apt_commands(self):
        payload = build_source_reinstall_json(("gcc",), "apt")

        self.assertEqual(payload["actions"][0]["command"], ["sudo", "apt-get", "remove", "-y", "gcc"])
        self.assertEqual(payload["actions"][1]["command"], ["sudo", "apt-get", "install", "-y", "gcc"])

    def test_no_sudo_omits_sudo_prefix(self):
        payload = build_source_reinstall_json(("gcc",), "dnf", sudo=False)

        self.assertEqual(payload["actions"][0]["command"], ["dnf", "remove", "-y", "gcc"])
        self.assertEqual(payload["actions"][1]["command"], ["dnf", "install", "-y", "gcc"])

    def test_rejects_invalid_input(self):
        with self.assertRaisesRegex(ValueError, "upgrade_packages must be non-empty"):
            build_source_reinstall_json(("",), "dnf")
        with self.assertRaisesRegex(ValueError, "manager must be dnf or apt"):
            build_source_reinstall_json(("gcc",), "rpm")


class SourceReinstallCliTests(unittest.TestCase):
    def test_cli_writes_json_and_dry_runs_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "reinstall.txt"
            out_path = root / "out" / "reinstall.json"
            input_path.write_text("packages:\ngcc\nlibgcc\n", encoding="utf-8")

            with patch("specdeps.reinstall.source_cli.run_reinstall_actions", return_value=0) as apply:
                from specdeps.reinstall.source_cli import main

                exit_code = main(["--input", str(input_path), "--manager", "dnf", "--out", str(out_path)])

            payload = json.loads(out_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["install_source"], "configured_repo")
        self.assertEqual(payload["selected_packages"], ["gcc", "libgcc"])
        apply.assert_called_once_with(payload, execute=False)

    def test_cli_execute_runs_commands_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "reinstall.txt"
            out_path = Path(tmp) / "reinstall.json"
            input_path.write_text("packages:\ngcc\n", encoding="utf-8")

            with patch("specdeps.reinstall.apply_cli.subprocess.run") as run, patch("builtins.print"):
                from specdeps.reinstall.source_cli import main

                exit_code = main(["--input", str(input_path), "--out", str(out_path), "--execute"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["sudo", "dnf", "remove", "-y", "gcc"],
                ["sudo", "dnf", "install", "-y", "gcc"],
            ],
        )
        for call in run.call_args_list:
            self.assertTrue(call.kwargs["check"])

    def test_cli_reports_errors_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "reinstall.txt"
            input_path.write_text("packages:\n", encoding="utf-8")

            with patch("sys.stderr", io.StringIO()) as stderr:
                from specdeps.reinstall.source_cli import main

                with self.assertRaises(SystemExit) as error:
                    main(["--input", str(input_path)])

        self.assertEqual(error.exception.code, 2)
        self.assertIn("package section is empty", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_execute_reports_command_failure_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "reinstall.txt"
            input_path.write_text("packages:\ngcc\n", encoding="utf-8")
            failure = subprocess.CalledProcessError(1, ["sudo", "dnf", "remove", "-y", "gcc"])

            with patch("specdeps.reinstall.apply_cli.subprocess.run", side_effect=failure), patch(
                "sys.stderr",
                io.StringIO(),
            ) as stderr:
                from specdeps.reinstall.source_cli import main

                with self.assertRaises(SystemExit) as error:
                    main(["--input", str(input_path), "--out", str(Path(tmp) / "reinstall.json"), "--execute"])

        self.assertEqual(error.exception.code, 2)
        self.assertIn("command failed", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
