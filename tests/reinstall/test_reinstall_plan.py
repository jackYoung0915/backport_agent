import unittest
from pathlib import Path

from specdeps.models import PackagePathConfig, TopologyData
from specdeps.reinstall.plan import build_reinstall_actions, package_extension


class ReinstallPlanTests(unittest.TestCase):
    def test_builds_uninstall_then_install_actions(self):
        topology = TopologyData(
            repos=("app", "lib"),
            edges=(("app", "lib", "libpkg"),),
            packages_by_repo={"app": ("app", "app-devel"), "lib": ("libpkg",)},
        )
        config = PackagePathConfig(
            package_dirs={"app": (Path("/rpms/app"),), "lib": (Path("/rpms/lib"),)},
            package_names={},
            manager="dnf",
            sudo=True,
        )
        package_files_by_repo = {
            "app": (Path("/rpms/app/app-1.rpm"),),
            "lib": (Path("/rpms/lib/libpkg-1.rpm"),),
        }

        actions = build_reinstall_actions(topology, config, package_files_by_repo, None, False, False)

        self.assertEqual(
            [(action.phase, action.repo, action.command) for action in actions],
            [
                ("uninstall", "app", ("sudo", "dnf", "remove", "-y", "app", "app-devel")),
                ("uninstall", "lib", ("sudo", "dnf", "remove", "-y", "libpkg")),
                ("install", "lib", ("sudo", "dnf", "install", "-y", "/rpms/lib/libpkg-1.rpm")),
                ("install", "app", ("sudo", "dnf", "install", "-y", "/rpms/app/app-1.rpm")),
            ],
        )

    def test_no_sudo_rpm_manager_and_skip_uninstall(self):
        topology = TopologyData(
            repos=("app",),
            edges=(),
            packages_by_repo={"app": ("app",)},
        )
        config = PackagePathConfig(package_dirs={"app": (Path("/rpms/app"),)}, package_names={}, manager="rpm", sudo=False)
        package_files_by_repo = {"app": (Path("/rpms/app/app-1.rpm"),)}

        actions = build_reinstall_actions(topology, config, package_files_by_repo, ("app",), True, False)

        self.assertEqual(actions[0].command, ("rpm", "-Uvh", "/rpms/app/app-1.rpm"))

    def test_apt_manager_uses_deb_files_and_package_name_overrides(self):
        topology = TopologyData(
            repos=("app",),
            edges=(),
            packages_by_repo={"app": ("rpm-app-name",)},
        )
        config = PackagePathConfig(
            package_dirs={"app": (Path("/debs/app"),)},
            package_names={"app": ("deb-app-name",)},
            manager="apt",
            sudo=True,
        )
        package_files_by_repo = {"app": (Path("/debs/app/deb-app-name_1.0_arm64.deb"),)}

        actions = build_reinstall_actions(topology, config, package_files_by_repo, ("app",), False, False)

        self.assertEqual(actions[0].command, ("sudo", "apt-get", "remove", "-y", "deb-app-name"))
        self.assertEqual(actions[1].command, ("sudo", "apt-get", "install", "-y", "/debs/app/deb-app-name_1.0_arm64.deb"))
        self.assertEqual(package_extension("apt"), ".deb")

    def test_dpkg_manager_uses_dpkg_commands(self):
        topology = TopologyData(
            repos=("app",),
            edges=(),
            packages_by_repo={"app": ("app",)},
        )
        config = PackagePathConfig(package_dirs={"app": (Path("/debs/app"),)}, package_names={}, manager="dpkg", sudo=False)
        package_files_by_repo = {"app": (Path("/debs/app/app_1.0_arm64.deb"),)}

        actions = build_reinstall_actions(topology, config, package_files_by_repo, ("app",), False, False)

        self.assertEqual(actions[0].command, ("dpkg", "-r", "app"))
        self.assertEqual(actions[1].command, ("dpkg", "-i", "/debs/app/app_1.0_arm64.deb"))

    def test_missing_package_files_for_selected_repo_raises(self):
        topology = TopologyData(
            repos=("app",),
            edges=(),
            packages_by_repo={"app": ("app",)},
        )
        config = PackagePathConfig(package_dirs={}, package_names={}, manager="dnf", sudo=True)

        with self.assertRaisesRegex(ValueError, "missing package files"):
            build_reinstall_actions(topology, config, {}, None, False, False)

    def test_skip_install_does_not_require_package_files(self):
        topology = TopologyData(
            repos=("app",),
            edges=(),
            packages_by_repo={"app": ("app",)},
        )
        config = PackagePathConfig(package_dirs={}, package_names={}, manager="dnf", sudo=True)

        actions = build_reinstall_actions(topology, config, {}, None, False, True)

        self.assertEqual(tuple(action.phase for action in actions), ("uninstall",))

    def test_rejects_plan_with_no_actions(self):
        topology = TopologyData(
            repos=("app",),
            edges=(),
            packages_by_repo={"app": ("app",)},
        )
        config = PackagePathConfig(package_dirs={}, package_names={}, manager="dnf", sudo=True)

        with self.assertRaisesRegex(ValueError, "cannot skip both"):
            build_reinstall_actions(topology, config, {}, None, True, True)

    def test_selected_provider_with_external_dependent_requires_acknowledgement(self):
        topology = TopologyData(
            repos=("app", "lib", "other"),
            edges=(("app", "lib", "libpkg"), ("other", "lib", "libpkg")),
            packages_by_repo={"app": ("app",), "lib": ("libpkg",), "other": ("other",)},
        )
        config = PackagePathConfig(package_dirs={"lib": (Path("/rpms/lib"),)}, package_names={}, manager="dnf", sudo=True)
        package_files_by_repo = {"lib": (Path("/rpms/lib/libpkg-1.rpm"),)}

        with self.assertRaisesRegex(ValueError, "dependents outside reinstall set"):
            build_reinstall_actions(topology, config, package_files_by_repo, ("lib",), False, False)

        actions = build_reinstall_actions(
            topology,
            config,
            package_files_by_repo,
            ("lib",),
            False,
            False,
            allow_external_dependents=True,
        )
        self.assertEqual(tuple(action.repo for action in actions), ("lib", "lib"))


if __name__ == "__main__":
    unittest.main()
