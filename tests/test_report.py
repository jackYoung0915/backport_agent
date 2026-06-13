import unittest

from specdeps.graph import build_dependency_graph, render_mermaid
from specdeps.models import SpecInfo
from specdeps.report import render_report


class ReportTests(unittest.TestCase):
    def test_render_report_includes_mermaid_edges_and_external_requires(self):
        specs = [
            SpecInfo(
                repo="src-openEuler-qemu",
                spec_path="work/repos/src-openEuler-qemu/qemu.spec",
                source_name="qemu",
                packages=frozenset({"qemu"}),
                provides=frozenset(),
                requires=("libvirt", "glibc"),
            ),
            SpecInfo(
                repo="src-openEuler-libvirt",
                spec_path="work/repos/src-openEuler-libvirt/libvirt.spec",
                source_name="libvirt",
                packages=frozenset({"libvirt"}),
                provides=frozenset(),
                requires=(),
            ),
        ]
        graph = build_dependency_graph(specs)
        report = render_report(graph, specs, render_mermaid(graph))

        self.assertIn("# RPM Spec Install Dependency Topology", report)
        self.assertIn("| src-openEuler-qemu | src-openEuler-libvirt | libvirt |", report)
        self.assertIn("- `src-openEuler-qemu`: `glibc`", report)
        self.assertIn("```mermaid", report)
        self.assertIn("src_openEuler_qemu -->|libvirt| src_openEuler_libvirt", report)


if __name__ == "__main__":
    unittest.main()
