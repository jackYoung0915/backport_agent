import json
import tempfile
import unittest
from pathlib import Path

from specdeps.repo_config import load_repos, normalize_repo_url


class RepoConfigTests(unittest.TestCase):
    def test_normalize_gitcode_tree_url(self):
        repo = normalize_repo_url(
            "https://gitcode.com/kunpengcompute/src-openEuler-qemu/tree/openEuler-24.03-LTS-SP3_velinux"
        )

        self.assertEqual(repo.name, "src-openEuler-qemu")
        self.assertEqual(repo.branch, "openEuler-24.03-LTS-SP3_velinux")
        self.assertEqual(repo.clone_url, "https://gitcode.com/kunpengcompute/src-openEuler-qemu.git")
        self.assertEqual(
            repo.page_url,
            "https://gitcode.com/kunpengcompute/src-openEuler-qemu/tree/openEuler-24.03-LTS-SP3_velinux",
        )

    def test_load_repos_preserves_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "repos.json"
            config_path.write_text(
                json.dumps(
                    [
                        {
                            "url": "https://gitcode.com/kunpengcompute/src-openEuler-ubutils/tree/openEuler-24.03-LTS-SP3_velinux"
                        },
                        {
                            "url": "https://gitcode.com/kunpengcompute/src-openEuler-ubctl/tree/openEuler-24.03-LTS-SP3_velinux"
                        },
                    ]
                ),
                encoding="utf-8",
            )

            repos = load_repos(config_path)

        self.assertEqual([repo.name for repo in repos], ["src-openEuler-ubutils", "src-openEuler-ubctl"])
        self.assertEqual(repos[1].branch, "openEuler-24.03-LTS-SP3_velinux")

    def test_load_repos_rejects_missing_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "repos.json"
            config_path.write_text(json.dumps([{"name": "broken"}]), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "entry 0 must contain a url"):
                load_repos(config_path)


if __name__ == "__main__":
    unittest.main()
