import subprocess
import sys
import unittest


class ReinstallCompatibilityTests(unittest.TestCase):
    def test_public_imports_stay_available(self):
        from specdeps.package_metadata import load_package_metadata
        from specdeps.reinstall_json import build_reinstall_json
        from specdeps.reinstall_plan import build_reinstall_actions
        from specdeps.reinstall_txt import parse_reinstall_txt

        self.assertTrue(callable(load_package_metadata))
        self.assertTrue(callable(build_reinstall_json))
        self.assertTrue(callable(build_reinstall_actions))
        self.assertTrue(callable(parse_reinstall_txt))

    def test_public_module_help_entrypoints_stay_available(self):
        for module in (
            "specdeps.reinstall_cli",
            "specdeps.reinstall_json_cli",
            "specdeps.reinstall_txt_cli",
            "specdeps.reinstall_iso_cli",
            "specdeps.reinstall_apply_cli",
        ):
            with self.subTest(module=module):
                result = subprocess.run(
                    [sys.executable, "-m", module, "--help"],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self.assertIn("usage:", result.stdout)


if __name__ == "__main__":
    unittest.main()
