import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from specdeps.reinstall.package_metadata import PackageMetadata, load_package_metadata
from specdeps.reinstall.json_plan import build_reinstall_json, discover_upgrade_artifacts, load_reinstall_input


def _find_lintian_deb() -> Path | None:
    override = os.environ.get("LINTIAN_DEB")
    if override:
        candidate = Path(override)
        return candidate if candidate.is_file() else None
    for directory in (Path("/home/debs"), Path("/var/cache/apt/archives")):
        if directory.is_dir():
            matches = sorted(directory.glob("lintian_*.deb"))
            if matches:
                return matches[0]
    return None


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

    def test_strips_accidental_package_label_from_selected_names(self):
        # provider_index keys are clean names from dpkg-deb/rpm; a selected name
        # carrying a stray "Package:" label must still match rather than raise
        # "upgrade package not found".
        packages = [
            PackageMetadata(
                name="app",
                artifact=Path("/packages/app.deb"),
                provides=("app",),
                requires=(),
            )
        ]

        for selected in ("Package: app", "Package：app", "package:app"):
            with self.subTest(selected=selected):
                payload = build_reinstall_json((selected,), packages, "deb")
                self.assertEqual(payload["selected_packages"], ["app"])

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

            with patch("specdeps.reinstall.json_cli.load_package_metadata", return_value=metadata):
                from specdeps.reinstall.json_cli import main

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
                from specdeps.reinstall.json_cli import main

                main(["--input", str(input_path)])

        self.assertEqual(error.exception.code, 2)
        self.assertIn("does not exist", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


@unittest.skipUnless(shutil.which("dpkg-deb"), "dpkg-deb is required for real .deb integration test")
class ReinstallJsonLintianIntegrationTests(unittest.TestCase):
    """Full pipeline against a real lintian .deb: discover -> metadata -> JSON."""

    @classmethod
    def setUpClass(cls):
        cls.deb = _find_lintian_deb()
        if cls.deb is None:
            raise unittest.SkipTest(
                "no lintian .deb found; run 'apt-get download lintian' into /home/debs "
                "or set LINTIAN_DEB"
            )

    def test_builds_reinstall_json_for_real_lintian_package(self):
        package_format, artifacts = discover_upgrade_artifacts((self.deb,))
        self.assertEqual(package_format, "deb")
        metadata = [load_package_metadata(artifact, package_format) for artifact in artifacts]

        payload = build_reinstall_json(
            ("lintian",), metadata, package_format, include_available_dependencies=False,
        )

        self.assertEqual(payload["selected_packages"], ["lintian"])
        self.assertEqual(payload["manager"], "apt")
        self.assertEqual(payload["order"]["install"], ["lintian"])
        lintian_record = next(p for p in payload["packages"] if p["name"] == "lintian")
        self.assertEqual(lintian_record["artifact"], str(self.deb))

    def test_stray_package_prefix_still_resolves_against_real_metadata(self):
        # Reproduces the original "找不到包" report: a selected name carrying a
        # "Package:" label must resolve against provider_index keyed by the real
        # (clean) package name.
        package_format, artifacts = discover_upgrade_artifacts((self.deb,))
        metadata = [load_package_metadata(artifact, package_format) for artifact in artifacts]

        payload = build_reinstall_json(
            ("Package: lintian",), metadata, package_format, include_available_dependencies=False,
        )

        self.assertEqual(payload["selected_packages"], ["lintian"])


if __name__ == "__main__":
    unittest.main()
