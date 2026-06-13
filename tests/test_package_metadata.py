import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from specdeps.package_metadata import PackageMetadata, load_package_metadata


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

        with patch("specdeps.package_metadata.subprocess.run", side_effect=fake_run):
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
        def fake_run(command, check, stdout, text):
            self.assertEqual(command, ["dpkg-deb", "-f", "/packages/app.deb", "Package", "Provides", "Depends", "Pre-Depends"])
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="app\napp-virtual\nlibfoo (>= 1.0), libc6 | libc6.1\nbase-files\n",
            )

        with patch("specdeps.package_metadata.subprocess.run", side_effect=fake_run):
            metadata = load_package_metadata(Path("/packages/app.deb"), "deb")

        self.assertEqual(metadata.name, "app")
        self.assertEqual(metadata.provides, ("app", "app-virtual"))
        self.assertEqual(metadata.requires, ("libfoo", "libc6", "libc6.1", "base-files"))

    def test_reports_missing_metadata_tool(self):
        with patch("specdeps.package_metadata.subprocess.run", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(RuntimeError, "rpm command not found"):
                load_package_metadata(Path("/packages/app.rpm"), "rpm")


if __name__ == "__main__":
    unittest.main()
