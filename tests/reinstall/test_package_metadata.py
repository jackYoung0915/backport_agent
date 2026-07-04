import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from specdeps.reinstall.package_metadata import PackageMetadata, load_package_metadata


def _find_lintian_deb() -> Path | None:
    """Locate a lintian .deb fixture for integration testing.

    Search order: LINTIAN_DEB env var, /home/debs, then the apt download cache.
    """
    override = os.environ.get("LINTIAN_DEB")
    if override:
        candidate = Path(override)
        return candidate if candidate.is_file() else None
    search_dirs = [Path("/home/debs"), Path("/var/cache/apt/archives")]
    for directory in search_dirs:
        if directory.is_dir():
            matches = sorted(directory.glob("lintian_*.deb"))
            if matches:
                return matches[0]
    return None


class PackageMetadataTests(unittest.TestCase):
    def test_loads_rpm_metadata_and_normalizes_requires(self):
        def fake_run(command, check, stdout, text):
            self.assertEqual(command[:3], ["rpm", "-qp", "--queryformat"])
            self.assertTrue(check)
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="NAME\napp\nPROVIDES\napp\napp-virtual\nREQUIRES\nlibfoo = 1.0\n/usr/bin/sh\nrpmlib(PayloadFilesHavePrefix)\n",
            )

        with patch("specdeps.reinstall.package_metadata.subprocess.run", side_effect=fake_run):
            metadata = load_package_metadata(Path("/packages/app-1.rpm"), "rpm")

        self.assertEqual(
            metadata,
            PackageMetadata(
                name="app",
                artifact=Path("/packages/app-1.rpm"),
                provides=("app", "app-virtual"),
                requires=("libfoo", "sh"),
            ),
        )

    def test_loads_deb_metadata_and_normalizes_dependencies(self):
        # "dpkg-deb -f" with multiple fields emits a "Field: value" control
        # stanza (not bare values). Depends folds onto a continuation line.
        def fake_run(command, check, stdout, text):
            self.assertEqual(
                command,
                ["dpkg-deb", "-f", "/packages/app.deb", "Package", "Provides", "Depends", "Pre-Depends", "Version", "Architecture"],
            )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "Package: app\n"
                    "Provides: app-virtual\n"
                    "Depends: libfoo (>= 1.0), libc6 | libc6.1,\n"
                    " libbar\n"
                    "Pre-Depends: base-files\n"
                    "Version: 1.0-1\n"
                    "Architecture: amd64\n"
                ),
            )

        with patch("specdeps.reinstall.package_metadata.subprocess.run", side_effect=fake_run):
            metadata = load_package_metadata(Path("/packages/app.deb"), "deb")

        self.assertEqual(metadata.name, "app")
        self.assertEqual(metadata.provides, ("app", "app-virtual"))
        self.assertEqual(metadata.requires, ("libfoo", "libc6", "libc6.1", "libbar", "base-files"))
        self.assertEqual(metadata.version, "1.0-1")
        self.assertEqual(metadata.arch, "amd64")

    def test_loads_deb_metadata_when_optional_fields_absent(self):
        # dpkg-deb omits absent fields entirely; positional parsing would shift
        # the "Package:" label into the name. Ensure field-name parsing holds.
        def fake_run(command, check, stdout, text):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="Package: lintian\nVersion: 2.117.0\nArchitecture: all\n",
            )

        with patch("specdeps.reinstall.package_metadata.subprocess.run", side_effect=fake_run):
            metadata = load_package_metadata(Path("/packages/lintian.deb"), "deb")

        self.assertEqual(metadata.name, "lintian")
        self.assertEqual(metadata.provides, ("lintian",))
        self.assertEqual(metadata.requires, ())
        self.assertEqual(metadata.version, "2.117.0")
        self.assertEqual(metadata.arch, "all")

    def test_reports_missing_metadata_tool(self):
        with patch("specdeps.reinstall.package_metadata.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(RuntimeError, "rpm command not found"):
                load_package_metadata(Path("/packages/app.rpm"), "rpm")


@unittest.skipUnless(shutil.which("dpkg-deb"), "dpkg-deb is required for real .deb integration test")
class LintianDebIntegrationTests(unittest.TestCase):
    """End-to-end metadata parsing against a real lintian .deb (no mocking)."""

    @classmethod
    def setUpClass(cls):
        cls.deb = _find_lintian_deb()
        if cls.deb is None:
            raise unittest.SkipTest(
                "no lintian .deb found; run 'apt-get download lintian' into /home/debs "
                "or set LINTIAN_DEB"
            )

    def test_parses_real_lintian_metadata_without_label_leakage(self):
        metadata = load_package_metadata(self.deb, "deb")

        # Real dpkg-deb output labels every field and omits absent ones
        # (lintian has no Provides/Pre-Depends). The parser must key by field
        # name so no "Package:"/"Depends:" label leaks into values.
        self.assertEqual(metadata.name, "lintian")
        self.assertEqual(metadata.arch, "all")
        self.assertTrue(metadata.version, "version should be populated")
        self.assertEqual(metadata.provides, ("lintian",))

        leaked = [
            value
            for value in (metadata.name, metadata.version, metadata.arch, *metadata.provides, *metadata.requires)
            if ":" in value and value.split(":", 1)[0] in {"Package", "Provides", "Depends", "Pre-Depends", "Version", "Architecture"}
        ]
        self.assertEqual(leaked, [], f"control-field label leaked into metadata: {leaked}")

        # lintian has real dependencies; version constraints must be stripped.
        self.assertIn("dpkg", metadata.requires)
        self.assertTrue(all("(" not in requirement for requirement in metadata.requires))


if __name__ == "__main__":
    unittest.main()
