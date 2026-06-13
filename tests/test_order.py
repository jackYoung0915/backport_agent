import unittest

from specdeps.models import TopologyData
from specdeps.order import dependency_closure, install_order, uninstall_order


class OrderTests(unittest.TestCase):
    def test_install_and_uninstall_order_follow_dependency_edges(self):
        topology = TopologyData(
            repos=("A", "B", "C"),
            edges=(("A", "B", "pkg-b"), ("B", "C", "pkg-c")),
            packages_by_repo={"A": ("a",), "B": ("b",), "C": ("c",)},
        )

        self.assertEqual(install_order(topology, None), ("C", "B", "A"))
        self.assertEqual(uninstall_order(topology, None), ("A", "B", "C"))

    def test_dependency_closure_includes_provider_repos(self):
        topology = TopologyData(
            repos=("A", "B", "C", "D"),
            edges=(("A", "B", "pkg-b"), ("B", "C", "pkg-c")),
            packages_by_repo={"A": ("a",), "B": ("b",), "C": ("c",), "D": ("d",)},
        )

        self.assertEqual(dependency_closure(topology, ("A",)), frozenset({"A", "B", "C"}))
        self.assertEqual(install_order(topology, ("A",)), ("C", "B", "A"))

    def test_self_dependencies_do_not_create_cycles(self):
        topology = TopologyData(
            repos=("A",),
            edges=(("A", "A", "pkg-a"),),
            packages_by_repo={"A": ("a",)},
        )

        self.assertEqual(install_order(topology, None), ("A",))
        self.assertEqual(uninstall_order(topology, None), ("A",))

    def test_unknown_selected_repo_raises_value_error(self):
        topology = TopologyData(
            repos=("A",),
            edges=(),
            packages_by_repo={"A": ("a",)},
        )

        with self.assertRaisesRegex(ValueError, "unknown repositories"):
            install_order(topology, ("missing",))

    def test_cycle_raises_value_error(self):
        topology = TopologyData(
            repos=("A", "B"),
            edges=(("A", "B", "pkg-b"), ("B", "A", "pkg-a")),
            packages_by_repo={"A": ("a",), "B": ("b",)},
        )

        with self.assertRaisesRegex(ValueError, "dependency cycle"):
            install_order(topology, None)


if __name__ == "__main__":
    unittest.main()
