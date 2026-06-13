import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from specdeps.package_metadata import PackageMetadata
from specdeps.reinstall_json import build_reinstall_json, discover_upgrade_artifacts, load_reinstall_input


class ReinstallJsonTests(unittest.TestCase):
    def test_builds_reinstall_json_from_seed_package_closure(self):
        packages = [
            PackageMetadata(
                name="app",
                artifact=Path("/packages/app-1.rpm"),
                provides=("app",),
                requires=("libfoo", "systemd"),
            ),
            PackageMetadata(
                name="libfoo",
                artifact=Path("/packages/libfoo-1.rpm"),
                provides=("libfoo",),
                requires=("glibc",),
            ),
            PackageMetadata(
                name="unrelated",
                artifact=Path("/packages/unrelated-1.rpm"),
                provides=("unrelated",),
                requires=(),
            ),
        ]

        payload = build_reinstall_json(("app",), packages, "rpm")

        self.assertEqual(payload["format"], "rpm")
        self.assertEqual(payload["manager"], "dnf")
        self.assertEqual(payload["selected_packages"], ["app"])
        self.assertEqual(payload["order"], {"uninstall": ["app", "libfoo"], "install": ["libfoo", "app"]})
        self.assertEqual(
            payload["dependency_edges"],
            [{"source": "app", "target": "libfoo", "require": "libfoo"}],
        )
        self.assertEqual(
            [(action["phase"], action["package"], action["command"]) for action in payload["actions"]],
            [
                ("uninstall", "app", ["sudo", "dnf", "remove", "-y", "app"]),
                ("uninstall", "libfoo", ["sudo", "dnf", "remove", "-y", "libfoo"]),
                ("install", "libfoo", ["sudo", "dnf", "install", "-y", "/packages/libfoo-1.rpm"]),
                ("install", "app", ["sudo", "dnf", "install", "-y", "/packages/app-1.rpm"]),
            ],
        )
        app_record = next(package for package in payload["packages"] if package["name"] == "app")
        self.assertEqual(app_record["boundary_requires"], ["systemd"])
        self.assertIn("app requires systemd outside upgrade set", payload["warnings"])
        self.assertNotIn("unrelated", [package["name"] for package in payload["packages"]])

    def test_builds_deb_commands(self):
        packages = [
            PackageMetadata(
                name="app",
                artifact=Path("/packages/app.deb"),
                provides=("app",),
                requires=(),
            )
        ]

        payload = build_reinstall_json(("app",), packages, "deb")

        self.assertEqual(payload["manager"], "apt")
        self.assertEqual(payload["actions"][0]["command"], ["sudo", "apt-get", "remove", "-y", "app"])
        self.assertEqual(payload["actions"][1]["command"], ["sudo", "apt-get", "install", "-y", "/packages/app.deb"])

    def test_rejects_missing_seed_package(self):
        packages = [
            PackageMetadata(
                name="libfoo",
                artifact=Path("/packages/libfoo-1.rpm"),
                provides=("libfoo",),
                requires=(),
            )
        ]

        with self.assertRaisesRegex(ValueError, "upgrade package not found"):
            build_reinstall_json(("app",), packages, "rpm")

    def test_discovers_upgrade_artifacts_from_files_and_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package_dir = root / "packages"
            package_dir.mkdir()
            rpm = package_dir / "app-1.rpm"
            deb = root / "app.deb"
            source_rpm = package_dir / "app-1.src.rpm"
            rpm.write_text("", encoding="utf-8")
            deb.write_text("", encoding="utf-8")
            source_rpm.write_text("", encoding="utf-8")

            artifacts = discover_upgrade_artifacts((package_dir, rpm))

        self.assertEqual(artifacts, ("rpm", (rpm,)))

    def test_rejects_mixed_rpm_and_deb_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rpm = root / "app.rpm"
            deb = root / "app.deb"
            rpm.write_text("", encoding="utf-8")
            deb.write_text("", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "cannot mix RPM and DEB"):
                discover_upgrade_artifacts((rpm, deb))

    def test_load_reinstall_input_validates_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            path.write_text(json.dumps({"upgrade_packages": ["app"], "upgrade_paths": ["/packages"]}), encoding="utf-8")

            config = load_reinstall_input(path)

        self.assertEqual(config.upgrade_packages, ("app",))
        self.assertEqual(config.upgrade_paths, (Path("/packages"),))

    def test_load_reinstall_input_rejects_missing_lists(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            path.write_text(json.dumps({"upgrade_packages": "app", "upgrade_paths": []}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "upgrade_packages must be a non-empty list"):
                load_reinstall_input(path)


class ReinstallJsonCliTests(unittest.TestCase):
    def test_cli_writes_only_reinstall_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packages = root / "packages"
            packages.mkdir()
            (packages / "app-1.rpm").write_text("", encoding="utf-8")
            input_path = root / "input.json"
            out_path = root / "out" / "reinstall.json"
            input_path.write_text(
                json.dumps({"upgrade_packages": ["app"], "upgrade_paths": [str(packages)]}),
                encoding="utf-8",
            )
            metadata = PackageMetadata(
                name="app",
                artifact=packages / "app-1.rpm",
                provides=("app",),
                requires=(),
            )

            with patch("specdeps.reinstall_json_cli.load_package_metadata", return_value=metadata):
                from specdeps.reinstall_json_cli import main

                exit_code = main(["--input", str(input_path), "--out", str(out_path)])

            payload = json.loads(out_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["actions"][1]["artifact"], str(metadata.artifact))
        self.assertFalse((out_path.parent / "dependencies.json").exists())
        self.assertFalse((out_path.parent / "dependency-topology.mmd").exists())
        self.assertFalse((out_path.parent / "dependency-topology.dot").exists())
        self.assertFalse((out_path.parent / "dependency-report.md").exists())

    def test_cli_reports_errors_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "missing.json"
            stderr = __import__("io").StringIO()

            with patch("sys.stderr", stderr), self.assertRaises(SystemExit) as error:
                from specdeps.reinstall_json_cli import main

                main(["--input", str(input_path)])

        self.assertEqual(error.exception.code, 2)
        self.assertIn("does not exist", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
