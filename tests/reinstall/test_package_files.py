import tempfile
import unittest
from pathlib import Path

from specdeps.reinstall.package_files import discover_package_files


class PackageFileTests(unittest.TestCase):
    def test_discovers_binary_rpms_from_multiple_dirs_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            first.mkdir()
            second.mkdir()
            (first / "libummu-devel-1.0-1.aarch64.rpm").write_text("", encoding="utf-8")
            (second / "libummu-1.0-1.aarch64.rpm").write_text("", encoding="utf-8")
            (second / "libummu-1.0-1.src.rpm").write_text("", encoding="utf-8")
            (second / "libummu_1.0_arm64.deb").write_text("", encoding="utf-8")
            (second / "notes.txt").write_text("", encoding="utf-8")

            packages = discover_package_files({"src-openEuler-ummu": (first, second)}, ".rpm")

        self.assertEqual(
            [path.name for path in packages["src-openEuler-ummu"]],
            ["libummu-1.0-1.aarch64.rpm", "libummu-devel-1.0-1.aarch64.rpm"],
        )

    def test_discovers_debs_when_extension_is_deb(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            root.mkdir(exist_ok=True)
            (root / "libummu_1.0_arm64.deb").write_text("", encoding="utf-8")
            (root / "libummu-1.0-1.aarch64.rpm").write_text("", encoding="utf-8")

            packages = discover_package_files({"src-openEuler-ummu": (root,)}, ".deb")

        self.assertEqual([path.name for path in packages["src-openEuler-ummu"]], ["libummu_1.0_arm64.deb"])

    def test_missing_directory_raises(self):
        with self.assertRaisesRegex(FileNotFoundError, "does not exist"):
            discover_package_files({"src-openEuler-ummu": (Path("/missing/packages"),)}, ".rpm")


if __name__ == "__main__":
    unittest.main()
