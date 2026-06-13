import json
import tempfile
import unittest
from pathlib import Path

from specdeps.topology import load_topology


class TopologyTests(unittest.TestCase):
    def test_load_topology_extracts_repos_edges_and_packages(self):
        payload = {
            "repos": ["src-openEuler-cdma", "src-openEuler-ummu"],
            "edges": [
                {
                    "source_repo": "src-openEuler-cdma",
                    "target_repo": "src-openEuler-ummu",
                    "dependency": "libummu",
                }
            ],
            "specs": [
                {
                    "repo": "src-openEuler-cdma",
                    "packages": ["libcdma", "libcdma-devel"],
                    "provides": [],
                    "requires": ["libummu"],
                    "source_name": "libcdma",
                    "spec_path": "work/repos/src-openEuler-cdma/cdma.spec",
                },
                {
                    "repo": "src-openEuler-ummu",
                    "packages": ["libummu", "libummu-devel"],
                    "provides": [],
                    "requires": [],
                    "source_name": "libummu",
                    "spec_path": "work/repos/src-openEuler-ummu/ummu.spec",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            topology_path = Path(tmp) / "dependencies.json"
            topology_path.write_text(json.dumps(payload), encoding="utf-8")

            topology = load_topology(topology_path)

        self.assertEqual(topology.repos, ("src-openEuler-cdma", "src-openEuler-ummu"))
        self.assertEqual(topology.edges, (("src-openEuler-cdma", "src-openEuler-ummu", "libummu"),))
        self.assertEqual(topology.packages_by_repo["src-openEuler-cdma"], ("libcdma", "libcdma-devel"))
        self.assertEqual(topology.packages_by_repo["src-openEuler-ummu"], ("libummu", "libummu-devel"))

    def test_load_topology_aggregates_packages_from_multiple_specs_per_repo(self):
        payload = {
            "repos": ["repo"],
            "edges": [],
            "specs": [
                {
                    "repo": "repo",
                    "packages": ["pkg-a", "pkg-common"],
                    "provides": [],
                    "requires": [],
                    "source_name": "pkg-a",
                    "spec_path": "repo/a.spec",
                },
                {
                    "repo": "repo",
                    "packages": ["pkg-b", "pkg-common"],
                    "provides": [],
                    "requires": [],
                    "source_name": "pkg-b",
                    "spec_path": "repo/b.spec",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            topology_path = Path(tmp) / "dependencies.json"
            topology_path.write_text(json.dumps(payload), encoding="utf-8")

            topology = load_topology(topology_path)

        self.assertEqual(topology.packages_by_repo["repo"], ("pkg-a", "pkg-common", "pkg-b"))

    def test_load_topology_rejects_missing_specs(self):
        with tempfile.TemporaryDirectory() as tmp:
            topology_path = Path(tmp) / "dependencies.json"
            topology_path.write_text(json.dumps({"repos": ["src-openEuler-cdma"], "edges": []}), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "missing specs"):
                load_topology(topology_path)


if __name__ == "__main__":
    unittest.main()
