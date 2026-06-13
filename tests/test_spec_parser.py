import tempfile
import textwrap
import unittest
from pathlib import Path

from specdeps.spec_parser import dependency_names, expand_macros, parse_spec


class SpecParserTests(unittest.TestCase):
    def test_expand_macros_handles_simple_and_optional_forms(self):
        macros = {"name": "ubutils", "version": "1.2.3"}

        self.assertEqual(expand_macros("%{name} = %{version}", macros), "ubutils = 1.2.3")
        self.assertEqual(expand_macros("%{?name}-devel", macros), "ubutils-devel")
        self.assertEqual(expand_macros("%{?_isa}", macros), "")

    def test_dependency_names_ignores_versions_and_boolean_words(self):
        names = dependency_names("ubs-comm >= 1.0, (ubutils if qemu), libvirt%{?_isa}", {})

        self.assertEqual(names, ["ubs-comm", "ubutils", "qemu", "libvirt"])

    def test_dependency_names_normalizes_file_requirements(self):
        names = dependency_names("/usr/bin/qemu-img, /usr/sbin/virtqemud", {})

        self.assertEqual(names, ["qemu-img", "virtqemud"])

    def test_parse_spec_ignores_descriptive_provides_text(self):
        content = """
        Name:           ubs-comm
        Version:        1.0.0
        Release:        1
        Provides:       Huawei Technologies Co., Ltd
        Provides:       ubs-comm-lib = %{version}
        """

        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "hcom.spec"
            spec_path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")

            info = parse_spec(spec_path, "src-openEuler-ubs-comm")

        self.assertEqual(info.provides, frozenset({"ubs-comm-lib"}))

    def test_parse_spec_collects_packages_provides_and_runtime_requires(self):
        content = """
        %global common_pkg ubs-comm
        Name:           ubs-engine
        Version:        1.0.0
        Release:        1
        Summary:        fixture
        BuildRequires:  gcc
        Requires:       %{common_pkg} >= 1.0
        Requires(post): ubutils

        %package devel
        Summary:        development files
        Requires:       %{name}%{?_isa} = %{version}-%{release}

        %package -n libubsengine
        Summary:        library
        Provides:       ubs-engine-lib = %{version}
        Requires(preun): libvirt >= 9.0
        """

        with tempfile.TemporaryDirectory() as tmp:
            spec_path = Path(tmp) / "ubs-engine.spec"
            spec_path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")

            info = parse_spec(spec_path, "src-openEuler-ubs-engine")

        self.assertEqual(info.repo, "src-openEuler-ubs-engine")
        self.assertEqual(info.source_name, "ubs-engine")
        self.assertEqual(
            info.packages,
            frozenset({"ubs-engine", "ubs-engine-devel", "libubsengine"}),
        )
        self.assertEqual(info.provides, frozenset({"ubs-engine-lib"}))
        self.assertEqual(info.requires, ("ubs-comm", "ubutils", "ubs-engine", "libvirt"))


if __name__ == "__main__":
    unittest.main()
