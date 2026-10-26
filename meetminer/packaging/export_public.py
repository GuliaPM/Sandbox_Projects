"""Copy the publishable part of this project into publish/meetminer/, scan the copy, and refuse to leave it behind
if the scan finds anything.

    python packaging/export_public.py

Only the files named in INCLUDE are copied (an allowlist, so a new private file is never picked up by accident).
publish/ is git-ignored. Copy publish/meetminer/ into your clone of the GitHub repository and commit it from there.
publish/.github/ holds an optional test workflow for the repository root; it has not been run on GitHub.
"""
from __future__ import annotations

import argparse
import fnmatch
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import scan_release  # noqa: E402

FOLDER_NAME = "meetminer"
INCLUDE_FILES = ["README.md", "CHANGELOG.md", "LICENSE", "pyproject.toml", ".gitignore", ".gitattributes"]
INCLUDE_DIRS = ["src", "tests", "evals", "samples", "packaging", "docs", "tools", "guardrails", "scorecards"]
SKIP = ["__pycache__", "*.pyc", "*.egg-info", "*.lnk", "_build", "_cache", "_*.csv", "last_results.json",
        "fake_last_request.json"]
# Scratch helpers in tools/ start with an underscore (tools/_screens.py); the package's own __init__.py must still ship.
REQUIRED = ["README.md", "LICENSE", "pyproject.toml", "src/meetminer/__init__.py", "src/meetminer/__main__.py",
            "src/meetminer/app.py", "src/meetminer/engine.py", "src/meetminer/config/default.yaml",
            "tests/test_core.py", "tests/test_guardrails.py", "samples/transcripts", "packaging/build.py",
            "docs/architecture.md", "guardrails/README.md", "scorecards/scorecard.spec.yaml",
            "scorecards/build_scorecard.py", "evals/OPS-01.md", "evals/probes/probes.yaml",
            "src/meetminer/retrieval.py", "tests/test_rag.py", "evals/retrieval_eval.py", "scorecards/scorecard-rag.spec.yaml",
            "guardrails/GR-RAG-01.md"]

WORKFLOW = f"""\
# Optional: runs the unit tests for {FOLDER_NAME}/ on every push or pull request that touches it.
# Copy this file to .github/workflows/ at the root of the repository. It has not been run on GitHub yet.
name: {FOLDER_NAME} tests

on:
  push:
    paths: ["{FOLDER_NAME}/**"]
  pull_request:
    paths: ["{FOLDER_NAME}/**"]

jobs:
  test:
    runs-on: windows-latest
    defaults:
      run:
        working-directory: {FOLDER_NAME}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: python -m pip install -e .
      - run: python -m unittest discover -s tests -v
"""


def skipped(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in SKIP)


def copy_tree(source: Path, target: Path) -> int:
    count = 0
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if any(skipped(part) for part in rel.parts):
            continue
        if source.name == "tools" and rel.name.startswith("_"):
            continue
        if path.is_file():
            (target / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target / rel)
            count += 1
    return count


def export(out_root: Path) -> Path:
    target = out_root / FOLDER_NAME
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    count = 0
    for name in INCLUDE_FILES:
        if (ROOT / name).is_file():
            shutil.copy2(ROOT / name, target / name)
            count += 1
        else:
            print(f"  (no {name})")
    for name in INCLUDE_DIRS:
        count += copy_tree(ROOT / name, target / name)
    workflow = out_root / ".github" / "workflows" / f"{FOLDER_NAME}-tests.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(WORKFLOW, encoding="utf-8", newline="\n")
    missing = [name for name in REQUIRED if not (target / name).exists()]
    if missing:
        raise SystemExit(f"the export is missing required files: {', '.join(missing)}")
    print(f"copied {count} files to {target}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(ROOT / "publish"), help="output folder (default: publish/)")
    args = parser.parse_args()
    out_root = Path(args.out).resolve()
    target = export(out_root)
    code = scan_release.scan_source(target)
    if code:
        shutil.rmtree(target)  # only the folder this script made; never leave a failed export lying around
        print(f"\nthe export was deleted because the scan failed ({target})")
        return code
    print(f"\nready: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
