"""Show every flagged citation and quote from the last eval run in full, with the closest source text.

    python evals/show_flagged.py         (after python evals/run_eval.py has saved a file in evals/results/)
    python evals/show_flagged.py --transcripts PATH     if the eval was run on another folder
    python evals/show_flagged.py --results evals/results/quality-....json     a particular run instead of the newest
"""
from __future__ import annotations

import argparse
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))  # lets this run from a checkout without installing the package
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from meetminer.engine import load_settings  # noqa: E402
from meetminer.transcripts import _norm, check_citations, load_meetings  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"


def closest(quote: str, meetings) -> str:
    q = _norm(quote)
    best = (0, None, None, None)
    for m in meetings:
        for t in m.turns:
            match = SequenceMatcher(None, q, t.norm, autojunk=False).find_longest_match(0, len(q), 0, len(t.norm))
            if match.size > best[0]:
                best = (match.size, m, t, match)
    size, m, t, match = best
    if t is None:
        return "no overlap"
    start = max(0, match.b - 40)
    return (f"longest shared run {size}/{len(q)} chars in {m.title}, {t.label}, {t.start}: "
            f"...{t.norm[start:match.b + size + 60]}...")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--transcripts", default=str(ROOT / "samples" / "transcripts"))
    parser.add_argument("--results", help="a quality-....json file (default: the newest in evals/results/)")
    args = parser.parse_args()
    newest = sorted(RESULTS.glob("quality-*.json"), key=lambda p: p.stat().st_mtime)
    path = Path(args.results) if args.results else (newest[-1] if newest else None)
    if path is None or not path.is_file():
        print("No eval results found. Run  python evals/run_eval.py  first.")
        return 1
    settings = load_settings()
    meetings = load_meetings(Path(args.transcripts), settings.meetings_meta).meetings
    flagged = 0
    for r in json.loads(path.read_text(encoding="utf-8"))["outcomes"]:
        report = check_citations(r["answer"], meetings)
        lines = []
        for c in report.citations:
            if c.status == "ok":
                continue
            lines.append(f"  CITATION {c.status}: {c.raw}  problems={c.problems}")
            for q in c.quotes:
                if q.status in ("missing", "elsewhere"):
                    lines.append(f"     quote[{q.status}]: {q.text!r}")
                    if q.status == "missing":
                        lines.append(f"        closest: {closest(q.text, meetings)}")
        for q in report.uncited_quotes:
            if q.status == "missing":
                lines.append(f"  UNCITED quote: {q.text!r}\n        closest: {closest(q.text, meetings)}")
        if lines:
            flagged += 1
            print("=" * 80 + f"\n{r['id']} | failed checks: {r['failed_checks'] or 'none'}")
            print("\n".join(lines))
    print(f"\n{flagged} question(s) with flagged items.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
