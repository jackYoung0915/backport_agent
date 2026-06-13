import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from specdeps.reinstall_cli import main


class ReinstallCliTests(unittest.TestCase):
    def test_dry_run_prints_commands_without_executing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            topology = root / "dependencies.json"
            repo_dir = root / "rpms" / "lib"
            repo_dir.mkdir(parents=True)
            (repo_dir / "libpkg-1.rpm").write_text("", encoding="utf-8")
            topology.write_text(
                json.dumps(
                    {
                        "repos": ["lib"],
                        "edges": [],
                        "specs": [
                            {
                                "repo": "lib",
                                "packages": ["libpkg"],
                                "provides": [],
                                "requires": [],
                                "source_name": "libpkg",
                                "spec_path": "lib.spec",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch("specdeps.reinstall_cli.subprocess.run") as run, patch("builtins.print") as print_:
                exit_code = main(
                    [
                        "--topology",
                        str(topology),
                        "--package-dir",
                        f"lib={repo_dir}",
                    ]
                )

        self.assertEqual(exit_code, 0)
        run.assert_not_called()
        printed = "\n".join(" ".join(str(part) for part in call.args) for call in print_.call_args_list)
        self.assertIn("DRY-RUN", printed)
        self.assertIn("sudo dnf remove -y libpkg", printed)
        self.assertIn("sudo dnf install -y", printed)

    def test_execute_runs_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            topology = root / "dependencies.json"
            repo_dir = root / "rpms" / "lib"
            repo_dir.mkdir(parents=True)
            rpm = repo_dir / "libpkg-1.rpm"
            rpm.write_text("", encoding="utf-8")
            topology.write_text(
                json.dumps(
                    {
                        "repos": ["lib"],
                        "edges": [],
                        "specs": [
                            {
                                "repo": "lib",
                                "packages": ["libpkg"],
                                "provides": [],
                                "requires": [],
                                "source_name": "libpkg",
                                "spec_path": "lib.spec",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch("specdeps.reinstall_cli.subprocess.run") as run, patch("builtins.print"):
                exit_code = main(
                    [
                        "--topology",
                        str(topology),
                        "--package-dir",
                        f"lib={repo_dir}",
                        "--execute",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertEqual(run.call_count, 2)
        run.assert_any_call(("sudo", "dnf", "remove", "-y", "libpkg"), check=True)
        run.assert_any_call(("sudo", "dnf", "install", "-y", str(rpm)), check=True)

    def test_apt_dry_run_discovers_deb_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            topology = root / "dependencies.json"
            repo_dir = root / "debs" / "lib"
            repo_dir.mkdir(parents=True)
            (repo_dir / "libpkg_1.0_arm64.deb").write_text("", encoding="utf-8")
            (repo_dir / "libpkg-1.rpm").write_text("", encoding="utf-8")
            topology.write_text(
                json.dumps(
                    {
                        "repos": ["lib"],
                        "edges": [],
                        "specs": [
                            {
                                "repo": "lib",
                                "packages": ["libpkg"],
                                "provides": [],
                                "requires": [],
                                "source_name": "libpkg",
                                "spec_path": "lib.spec",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with patch("specdeps.reinstall_cli.subprocess.run") as run, patch("builtins.print") as print_:
                exit_code = main(
                    [
                        "--topology",
                        str(topology),
                        "--manager",
                        "apt",
                        "--package-dir",
                        f"lib={repo_dir}",
                    ]
                )

        self.assertEqual(exit_code, 0)
        run.assert_not_called()
        printed = "\n".join(" ".join(str(part) for part in call.args) for call in print_.call_args_list)
        self.assertIn("sudo apt-get remove -y libpkg", printed)
        self.assertIn("sudo apt-get install -y", printed)
        self.assertIn("libpkg_1.0_arm64.deb", printed)
        self.assertNotIn("libpkg-1.rpm", printed)

    def test_rejects_skip_uninstall_and_skip_install_together(self):
        with patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as error:
            main(["--skip-uninstall", "--skip-install"])

        self.assertEqual(error.exception.code, 2)

    def test_reports_planning_errors_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            topology = root / "dependencies.json"
            repo_dir = root / "rpms" / "lib"
            repo_dir.mkdir(parents=True)
            (repo_dir / "libpkg-1.rpm").write_text("", encoding="utf-8")
            topology.write_text(
                json.dumps(
                    {
                        "repos": ["app", "lib"],
                        "edges": [{"source_repo": "app", "target_repo": "lib", "dependency": "libpkg"}],
                        "specs": [
                            {
                                "repo": "app",
                                "packages": ["app"],
                                "provides": [],
                                "requires": ["libpkg"],
                                "source_name": "app",
                                "spec_path": "app.spec",
                            },
                            {
                                "repo": "lib",
                                "packages": ["libpkg"],
                                "provides": [],
                                "requires": [],
                                "source_name": "libpkg",
                                "spec_path": "lib.spec",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            stderr = io.StringIO()
            with patch("sys.stderr", stderr), self.assertRaises(SystemExit) as error:
                main(
                    [
                        "--topology",
                        str(topology),
                        "--only-repo",
                        "lib",
                        "--package-dir",
                        f"lib={repo_dir}",
                    ]
                )

        self.assertEqual(error.exception.code, 2)
        self.assertIn("dependents outside reinstall set", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
