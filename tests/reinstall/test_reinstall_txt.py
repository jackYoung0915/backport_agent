import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from specdeps.reinstall.txt_input import parse_package_only_txt, parse_reinstall_txt


class ReinstallTxtTests(unittest.TestCase):
    def test_parses_chinese_sections(self):
        text = """
        升级包名:
        app
        libfoo

        升级包路径:
        /srv/packages
        /srv/extra/libbar-1.0-1.aarch64.rpm
        """

        payload = parse_reinstall_txt(text)

        self.assertEqual(payload["upgrade_packages"], ["app", "libfoo"])
        self.assertEqual(payload["upgrade_paths"], ["/srv/packages", "/srv/extra/libbar-1.0-1.aarch64.rpm"])

    def test_parses_english_sections(self):
        text = """
        packages:
        app
        libfoo

        paths:
        /srv/packages
        /srv/extra/libbar-1.0-1.aarch64.rpm
        """

        payload = parse_reinstall_txt(text)

        self.assertEqual(payload["upgrade_packages"], ["app", "libfoo"])
        self.assertEqual(payload["upgrade_paths"], ["/srv/packages", "/srv/extra/libbar-1.0-1.aarch64.rpm"])

    def test_dedupes_values_preserving_first_seen_order(self):
        text = """
        packages:
        app
        app
        libfoo

        paths:
        /srv/packages
        /srv/packages
        /srv/extra
        """

        payload = parse_reinstall_txt(text)

        self.assertEqual(payload["upgrade_packages"], ["app", "libfoo"])
        self.assertEqual(payload["upgrade_paths"], ["/srv/packages", "/srv/extra"])

    def test_rejects_missing_package_section(self):
        with self.assertRaisesRegex(ValueError, "missing package section"):
            parse_reinstall_txt("paths:\n/srv/packages\n")

    def test_rejects_missing_path_section(self):
        with self.assertRaisesRegex(ValueError, "missing path section"):
            parse_reinstall_txt("packages:\napp\n")

    def test_rejects_unknown_section_header(self):
        text = """
        packages:
        app

        something else:
        value

        paths:
        /srv/packages
        """

        with self.assertRaisesRegex(ValueError, "unknown section header"):
            parse_reinstall_txt(text)

    def test_parses_package_only_chinese_sections(self):
        payload = parse_package_only_txt("升级包名:\napp\nlibfoo\n")

        self.assertEqual(payload, ["app", "libfoo"])

    def test_parses_package_only_english_sections(self):
        payload = parse_package_only_txt("packages:\napp\nlibfoo\n")

        self.assertEqual(payload, ["app", "libfoo"])

    def test_package_only_rejects_empty_package_section(self):
        with self.assertRaisesRegex(ValueError, "package section is empty"):
            parse_package_only_txt("packages:\n")

    def test_package_only_rejects_unknown_header(self):
        with self.assertRaisesRegex(ValueError, "unknown section header"):
            parse_package_only_txt("paths:\n/mnt/iso\n")


class ReinstallTxtCliTests(unittest.TestCase):
    def test_cli_writes_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "reinstall.txt"
            out_path = root / "config" / "reinstall-input.json"
            input_path.write_text("packages:\napp\n\npaths:\n/srv/packages\n", encoding="utf-8")

            from specdeps.reinstall.txt_cli import main

            exit_code = main(["--input", str(input_path), "--out", str(out_path)])

            payload = json.loads(out_path.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(payload, {"upgrade_packages": ["app"], "upgrade_paths": ["/srv/packages"]})

    def test_cli_reports_errors_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "reinstall.txt"
            input_path.write_text("packages:\napp\n", encoding="utf-8")
            stderr = io.StringIO()

            with patch("sys.stderr", stderr), self.assertRaises(SystemExit) as error:
                from specdeps.reinstall.txt_cli import main

                main(["--input", str(input_path)])

        self.assertEqual(error.exception.code, 2)
        self.assertIn("missing path section", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


class InstallScriptTests(unittest.TestCase):
    def test_install_script_is_beginner_friendly_and_non_executing(self):
        script = Path("scripts/install.sh")
        self.assertTrue(script.exists())
        mode = script.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)

        content = script.read_text(encoding="utf-8")

        self.assertIn(".venv", content)
        self.assertIn("pip install -e .", content)
        self.assertIn("specdeps-txt-to-json", content)
        self.assertIn("specdeps-reinstall-json", content)
        self.assertIn("command -v rpm", content)
        self.assertIn("command -v dpkg-deb", content)
        self.assertNotIn("dnf remove", content)
        self.assertNotIn("apt-get remove", content)


if __name__ == "__main__":
    unittest.main()
