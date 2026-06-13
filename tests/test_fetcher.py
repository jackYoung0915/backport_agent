import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from specdeps.fetcher import checkout_repos, find_spec_files
from specdeps.models import RepoRef


class FetcherTests(unittest.TestCase):
    def test_find_spec_files_prefers_root_specs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "nested"
            nested.mkdir()
            (root / "qemu.spec").write_text("Name: qemu\n", encoding="utf-8")
            (nested / "ignored.spec").write_text("Name: ignored\n", encoding="utf-8")

            specs = find_spec_files(root)

        self.assertEqual([path.name for path in specs], ["qemu.spec"])

    def test_find_spec_files_recurses_when_root_has_no_specs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "rpm"
            nested.mkdir()
            (nested / "libvirt.spec").write_text("Name: libvirt\n", encoding="utf-8")

            specs = find_spec_files(root)

        self.assertEqual([path.name for path in specs], ["libvirt.spec"])

    def test_checkout_repos_clones_missing_directory(self):
        repo = RepoRef(
            name="src-openEuler-qemu",
            page_url="https://gitcode.com/kunpengcompute/src-openEuler-qemu/tree/openEuler-24.03-LTS-SP3_velinux",
            clone_url="https://gitcode.com/kunpengcompute/src-openEuler-qemu.git",
            branch="openEuler-24.03-LTS-SP3_velinux",
        )

        with tempfile.TemporaryDirectory() as tmp, patch("specdeps.fetcher.subprocess.run") as run:
            paths = checkout_repos([repo], Path(tmp))

        self.assertEqual(paths["src-openEuler-qemu"], Path(tmp) / "src-openEuler-qemu")
        run.assert_called_once_with(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                "openEuler-24.03-LTS-SP3_velinux",
                "https://gitcode.com/kunpengcompute/src-openEuler-qemu.git",
                str(Path(tmp) / "src-openEuler-qemu"),
            ],
            check=True,
        )


if __name__ == "__main__":
    unittest.main()
