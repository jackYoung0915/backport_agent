import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from specdeps.reinstall.package_metadata import PackageMetadata


class IsoSourceTests(unittest.TestCase):
    def test_selects_only_requested_packages_from_iso_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            app_old = source / "app-1.0-1.x86_64.rpm"
            app_new = source / "app-2.0-1.x86_64.rpm"
            lib = source / "libfoo-3.0-1.x86_64.rpm"
            src = source / "app-2.0-1.src.rpm"
            for path in (app_old, app_new, lib, src):
                path.write_text("", encoding="utf-8")

            metadata_by_path = {
                app_old: PackageMetadata(
                    name="app",
                    artifact=app_old,
                    provides=("app",),
                    requires=("libfoo",),
                    version="1.0",
                    release="1",
                    arch="x86_64",
                ),
                app_new: PackageMetadata(
                    name="app",
                    artifact=app_new,
                    provides=("app",),
                    requires=("libfoo",),
                    version="2.0",
                    release="1",
                    arch="x86_64",
                ),
                lib: PackageMetadata(
                    name="libfoo",
                    artifact=lib,
                    provides=("libfoo",),
                    requires=(),
                    version="3.0",
                    release="1",
                    arch="x86_64",
                ),
            }

            with patch(
                "specdeps.reinstall.iso_source.load_package_metadata",
                side_effect=lambda path, package_format: metadata_by_path[Path(path)],
            ), patch("specdeps.reinstall.iso_source.platform.machine", return_value="x86_64"):
                from specdeps.reinstall.iso_source import build_iso_reinstall_json

                payload = build_iso_reinstall_json(("app",), (source,))

        self.assertEqual(payload["format"], "rpm")
        self.assertEqual([package["name"] for package in payload["packages"]], ["app"])
        self.assertEqual(payload["packages"][0]["artifact"], str(app_new))
        self.assertEqual(payload["packages"][0]["boundary_requires"], ["libfoo"])
        self.assertEqual(payload["order"], {"uninstall": ["app"], "install": ["app"]})
        self.assertEqual(payload["dependency_edges"], [])

    def test_generates_edges_when_dependency_is_explicitly_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            app = source / "app-1.0-1.x86_64.rpm"
            lib = source / "libfoo-1.0-1.x86_64.rpm"
            app.write_text("", encoding="utf-8")
            lib.write_text("", encoding="utf-8")
            metadata_by_path = {
                app: PackageMetadata("app", app, ("app",), ("libfoo",), version="1.0", release="1", arch="x86_64"),
                lib: PackageMetadata("libfoo", lib, ("libfoo",), (), version="1.0", release="1", arch="x86_64"),
            }

            with patch(
                "specdeps.reinstall.iso_source.load_package_metadata",
                side_effect=lambda path, package_format: metadata_by_path[Path(path)],
            ), patch("specdeps.reinstall.iso_source.platform.machine", return_value="x86_64"):
                from specdeps.reinstall.iso_source import build_iso_reinstall_json

                payload = build_iso_reinstall_json(("app", "libfoo"), (source,))

        self.assertEqual(payload["dependency_edges"], [{"source": "app", "target": "libfoo", "require": "libfoo"}])
        self.assertEqual(payload["order"], {"uninstall": ["app", "libfoo"], "install": ["libfoo", "app"]})

    def test_exact_name_wins_over_provides(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            exact = source / "tool-1.0-1.noarch.rpm"
            virtual = source / "provider-9.0-1.noarch.rpm"
            exact.write_text("", encoding="utf-8")
            virtual.write_text("", encoding="utf-8")
            metadata_by_path = {
                exact: PackageMetadata("tool", exact, ("tool",), (), version="1.0", release="1", arch="noarch"),
                virtual: PackageMetadata("provider", virtual, ("tool",), (), version="9.0", release="1", arch="noarch"),
            }

            with patch(
                "specdeps.reinstall.iso_source.load_package_metadata",
                side_effect=lambda path, package_format: metadata_by_path[Path(path)],
            ), patch("specdeps.reinstall.iso_source.platform.machine", return_value="x86_64"):
                from specdeps.reinstall.iso_source import select_requested_packages

                selected = select_requested_packages(("tool",), list(metadata_by_path.values()))

        self.assertEqual(selected, [metadata_by_path[exact]])

    def test_prefers_current_arch_then_noarch(self):
        packages = [
            PackageMetadata("app", Path("/p/app-3.aarch64.rpm"), ("app",), (), version="3", release="1", arch="aarch64"),
            PackageMetadata("app", Path("/p/app-2.noarch.rpm"), ("app",), (), version="2", release="1", arch="noarch"),
            PackageMetadata("app", Path("/p/app-1.x86_64.rpm"), ("app",), (), version="1", release="1", arch="x86_64"),
        ]

        with patch("specdeps.reinstall.iso_source.platform.machine", return_value="x86_64"):
            from specdeps.reinstall.iso_source import select_requested_packages

            selected = select_requested_packages(("app",), packages)

        self.assertEqual(selected, [packages[2]])

    def test_rejects_missing_requested_package(self):
        from specdeps.reinstall.iso_source import select_requested_packages

        with self.assertRaisesRegex(ValueError, "upgrade package not found in ISO source: app"):
            select_requested_packages(("app",), [])

    def test_rejects_mixed_rpm_and_deb_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "app.rpm").write_text("", encoding="utf-8")
            (source / "app.deb").write_text("", encoding="utf-8")

            from specdeps.reinstall.iso_source import discover_iso_artifacts

            with self.assertRaisesRegex(ValueError, "cannot mix RPM and DEB"):
                discover_iso_artifacts((source,))


if __name__ == "__main__":
    unittest.main()
