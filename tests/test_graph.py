import unittest

from specdeps.graph import build_dependency_graph, graph_to_dict, render_dot, render_mermaid
from specdeps.models import SpecInfo


class GraphTests(unittest.TestCase):
    def test_build_dependency_graph_maps_requires_to_repo_packages_and_provides(self):
        specs = [
            SpecInfo(
                repo="src-openEuler-ubs-engine",
                spec_path="ubs-engine.spec",
                source_name="ubs-engine",
                packages=frozenset({"ubs-engine", "libubsengine"}),
                provides=frozenset({"ubs-engine-lib"}),
                requires=("ubs-comm", "ubutils", "glibc"),
            ),
            SpecInfo(
                repo="src-openEuler-ubs-comm",
                spec_path="ubs-comm.spec",
                source_name="ubs-comm",
                packages=frozenset({"ubs-comm"}),
                provides=frozenset({"libubscomm"}),
                requires=("ubutils",),
            ),
            SpecInfo(
                repo="src-openEuler-ubutils",
                spec_path="ubutils.spec",
                source_name="ubutils",
                packages=frozenset({"ubutils"}),
                provides=frozenset(),
                requires=("bash",),
            ),
        ]

        graph = build_dependency_graph(specs)

        self.assertEqual(graph.repos, ("src-openEuler-ubs-comm", "src-openEuler-ubs-engine", "src-openEuler-ubutils"))
        self.assertEqual(
            [(edge.source_repo, edge.target_repo, edge.dependency) for edge in graph.edges],
            [
                ("src-openEuler-ubs-comm", "src-openEuler-ubutils", "ubutils"),
                ("src-openEuler-ubs-engine", "src-openEuler-ubs-comm", "ubs-comm"),
                ("src-openEuler-ubs-engine", "src-openEuler-ubutils", "ubutils"),
            ],
        )
        self.assertEqual(graph.external_requires["src-openEuler-ubs-engine"], ("glibc",))
        self.assertEqual(graph.external_requires["src-openEuler-ubutils"], ("bash",))

    def test_render_mermaid_and_dot(self):
        specs = [
            SpecInfo(
                repo="src-openEuler-qemu",
                spec_path="qemu.spec",
                source_name="qemu",
                packages=frozenset({"qemu"}),
                provides=frozenset(),
                requires=("libvirt",),
            ),
            SpecInfo(
                repo="src-openEuler-libvirt",
                spec_path="libvirt.spec",
                source_name="libvirt",
                packages=frozenset({"libvirt"}),
                provides=frozenset(),
                requires=(),
            ),
        ]
        graph = build_dependency_graph(specs)

        mermaid = render_mermaid(graph)
        dot = render_dot(graph)
        as_dict = graph_to_dict(graph)

        self.assertIn('src_openEuler_qemu["src-openEuler-qemu"]', mermaid)
        self.assertIn("src_openEuler_qemu -->|libvirt| src_openEuler_libvirt", mermaid)
        self.assertIn('"src-openEuler-qemu" -> "src-openEuler-libvirt" [label="libvirt"];', dot)
        self.assertEqual(as_dict["edges"][0]["dependency"], "libvirt")


if __name__ == "__main__":
    unittest.main()
