import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from specdeps.cli import main


class CliTests(unittest.TestCase):
    def test_cli_generates_outputs_from_existing_checkouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "repos.json"
            checkout_dir = root / "repos"
            out_dir = root / "out"
            qemu_repo = checkout_dir / "src-openEuler-qemu"
            libvirt_repo = checkout_dir / "src-openEuler-libvirt"
            qemu_repo.mkdir(parents=True)
            libvirt_repo.mkdir(parents=True)

            config_path.write_text(
                json.dumps(
                    [
                        {
                            "url": "https://gitcode.com/kunpengcompute/src-openEuler-qemu/tree/openEuler-24.03-LTS-SP3_velinux"
                        },
                        {
                            "url": "https://gitcode.com/kunpengcompute/src-openEuler-libvirt/tree/openEuler-24.03-LTS-SP3_velinux"
                        },
                    ]
                ),
                encoding="utf-8",
            )
            (qemu_repo / "qemu.spec").write_text(
                textwrap.dedent(
                    """
                    Name: qemu
                    Version: 1
                    Release: 1
                    Requires: libvirt >= 9.0
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            (libvirt_repo / "libvirt.spec").write_text(
                textwrap.dedent(
                    """
                    Name: libvirt
                    Version: 1
                    Release: 1
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            exit_code = main(
                [
                    "--config",
                    str(config_path),
                    "--checkout-dir",
                    str(checkout_dir),
                    "--out-dir",
                    str(out_dir),
                    "--skip-fetch",
                ]
            )

            dependencies = json.loads((out_dir / "dependencies.json").read_text(encoding="utf-8"))
            mermaid = (out_dir / "dependency-topology.mmd").read_text(encoding="utf-8")
            dot = (out_dir / "dependency-topology.dot").read_text(encoding="utf-8")
            report = (out_dir / "dependency-report.md").read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertEqual(dependencies["edges"][0]["source_repo"], "src-openEuler-qemu")
        self.assertEqual(dependencies["edges"][0]["target_repo"], "src-openEuler-libvirt")
        self.assertIn("src_openEuler_qemu -->|libvirt| src_openEuler_libvirt", mermaid)
        self.assertIn('"src-openEuler-qemu" -> "src-openEuler-libvirt" [label="libvirt"];', dot)
        self.assertIn("| src-openEuler-qemu | src-openEuler-libvirt | libvirt |", report)


if __name__ == "__main__":
    unittest.main()
