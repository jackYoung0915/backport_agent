import json
import tempfile
import unittest
from pathlib import Path

from specdeps.reinstall.package_config import load_package_config


class PackageConfigTests(unittest.TestCase):
    def test_load_config_and_cli_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "reinstall.json"
            config_path.write_text(
                json.dumps(
                    {
                        "manager": "dnf",
                        "sudo": True,
                        "package_dirs": {
                            "src-openEuler-ummu": ["/repo/ummu"],
                            "src-openEuler-cdma": "/repo/cdma-old",
                        },
                        "package_names": {
                            "src-openEuler-cdma": ["libcdma1", "libcdma-dev"]
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_package_config(
                config_path,
                ("src-openEuler-cdma=/repo/cdma-new", "src-openEuler-obmm=/repo/obmm"),
                manager_override="apt",
                sudo_override=False,
            )

        self.assertEqual(config.manager, "apt")
        self.assertFalse(config.sudo)
        self.assertEqual(config.package_dirs["src-openEuler-ummu"], (Path("/repo/ummu"),))
        self.assertEqual(config.package_dirs["src-openEuler-cdma"], (Path("/repo/cdma-new"),))
        self.assertEqual(config.package_dirs["src-openEuler-obmm"], (Path("/repo/obmm"),))
        self.assertEqual(config.package_names["src-openEuler-cdma"], ("libcdma1", "libcdma-dev"))

    def test_rejects_invalid_package_dir_override(self):
        with self.assertRaisesRegex(ValueError, "repo=path"):
            load_package_config(None, ("src-openEuler-cdma",), None, None)

    def test_rejects_non_boolean_sudo_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "reinstall.json"
            config_path.write_text(json.dumps({"sudo": "false"}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "sudo must be a boolean"):
                load_package_config(config_path, (), None, None)


if __name__ == "__main__":
    unittest.main()
