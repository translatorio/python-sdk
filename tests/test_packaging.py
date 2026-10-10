"""What the package promises about itself, rather than what it does.

Every failure here is silent for the person who hits it: a missing ``py.typed`` means a
consumer's type checker ignores every annotation in the client and nobody is told, and a
classifier list that lies about the Python versions is a promise no test would otherwise
check.
"""

from __future__ import annotations

import re
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT))


class PackagingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = (ROOT / "pyproject.toml").read_text()
        self.readme = (ROOT / "README.md").read_text()

    def test_the_name_and_the_import(self) -> None:
        self.assertIn('name = "translatorio-sdk"', self.manifest)
        self.assertIn('include = ["translatorio*"]', self.manifest)

    def test_the_typing_marker_is_shipped(self) -> None:
        self.assertTrue((ROOT / "translatorio" / "py.typed").exists())
        self.assertIn('translatorio = ["py.typed"]', self.manifest)
        self.assertIn("Typing :: Typed", self.manifest)

    def test_every_python_version_claimed_is_one_the_ci_runs(self) -> None:
        claimed = set(re.findall(r'Programming Language :: Python :: (3\.\d+)"', self.manifest))
        workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text()
        tested = set(re.findall(r"'(3\.\d+)'", workflow))

        self.assertTrue(claimed)
        self.assertEqual(claimed, tested, "the classifiers and the CI matrix disagree")

    def test_the_floor_is_the_lowest_version_tested(self) -> None:
        floor = re.search(r'requires-python = ">=(3\.\d+)"', self.manifest).group(1)
        tested = sorted(re.findall(r"'(3\.\d+)'", (ROOT / ".github" / "workflows" / "tests.yml").read_text()),
                        key=lambda v: [int(p) for p in v.split(".")])

        self.assertEqual(floor, tested[0], "requires-python claims a version the CI never runs")

    def test_it_declares_no_dependencies(self) -> None:
        self.assertIn("dependencies = []", self.manifest)

    def test_the_constant_and_the_manifest_agree(self) -> None:
        from translatorio import VERSION

        declared = re.search(r'^version = "([^"]+)"', self.manifest, re.M).group(1)

        self.assertEqual(declared, VERSION)
        self.assertRegex(VERSION, r"^\d+\.\d+\.\d+$")

    def test_the_publish_workflow_takes_the_version_from_a_tag_without_v(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text()

        self.assertIn("tags: ['[0-9]*.[0-9]*.[0-9]*']", workflow)
        self.assertIn("translatorio/client.py", workflow)
        # The sed it runs has to match the line it rewrites, or a release ships the old number.
        self.assertRegex((ROOT / "translatorio" / "client.py").read_text(), r'(?m)^VERSION = "')

    def test_the_readme_and_the_licence_travel_with_it(self) -> None:
        self.assertIn('readme = "README.md"', self.manifest)
        self.assertIn("Edu Lazaro", (ROOT / "LICENSE").read_text())
        self.assertIn('license = "MIT"', self.manifest)

    def test_the_readme_links_survive_pypi(self) -> None:
        # PyPI rewrites no relative path, so a relative link or image is broken there.
        self.assertNotIn("](LICENSE)", self.readme)
        self.assertIn("https://github.com/translatorio/python-sdk/blob/main/LICENSE", self.readme)
        self.assertIn("https://raw.githubusercontent.com/translatorio/python-sdk/main/art/banner.png", self.readme)

    def test_no_em_dashes(self) -> None:
        for path in [ROOT / "README.md", *(ROOT / "translatorio").glob("*.py")]:
            self.assertNotIn("—", path.read_text(), path.name)

    def test_the_package_docstring_is_valid_python(self) -> None:
        import translatorio

        example = translatorio.__doc__.split("\n", 2)[2]
        compile(textwrap.dedent(example), "<docstring>", "exec")


if __name__ == "__main__":
    unittest.main()
