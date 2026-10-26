"""Checks that what the repository says about itself stays true (no network, no key, no cost):
python -m unittest discover -s tests
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class CiClaimTests(unittest.TestCase):
    """GitHub Actions ran "meetminer tests" successfully on the 1.1.0 and 1.2.0 commits (GR-REL-09)."""

    def test_the_workflow_header_describes_what_it_does(self):
        export = load_script("export_public_docs", ROOT / "packaging" / "export_public.py")
        header = [ln for ln in export.WORKFLOW.splitlines() if ln.startswith("#")]
        self.assertEqual(len(header), 1)
        self.assertIn("every push and pull request", header[0])
        for stale in ("Copy this file", "has not been run", "Optional"):
            self.assertNotIn(stale, export.WORKFLOW)

    def test_no_document_says_ci_never_ran(self):
        for path in [ROOT / "README.md", *ROOT.glob("docs/*.md"), *ROOT.glob("guardrails/*.md"),
                     *ROOT.glob("scorecards/*.md"), ROOT / "packaging" / "export_public.py"]:
            text = path.read_text(encoding="utf-8").lower()
            for stale in ("never run on github", "not been run on github", "has not been run on github"):
                self.assertNotIn(stale, text, path.name)


if __name__ == "__main__":
    unittest.main()
