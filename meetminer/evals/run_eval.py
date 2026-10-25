"""Run evals/questions.yaml against the real Claude API and report pass/fail, citation checks and cost.

    python evals/run_eval.py --dry-run     show the cost estimate only (no key needed, no spend)
    python evals/run_eval.py               estimate, ask for confirmation, then run
    python evals/run_eval.py --yes --only launch-date,trap-competitor
    python evals/run_eval.py --model claude-sonnet-5-5 --yes      same eval on another model

By default it uses the fictional transcripts in samples/transcripts; --transcripts points it at another folder
(then the questions must be your own). It needs an API key: save one in the app's Settings, or set ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RESULTS = HERE / "results"
sys.path.insert(0, str(ROOT / "src"))  # lets this run from a checkout without installing the package

from meetminer.engine import (  # noqa: E402
    Answer, ConfigError, CostTracker, MeetingQA, cost_usd, has_credentials, load_env, load_settings, print_answer,
    with_overrides,
)
from meetminer.transcripts import load_meetings  # noqa: E402


def estimate(n: int, corpus_tokens: int, settings) -> tuple[float, float]:
    """(low, high) in USD. The first question writes the prompt cache, the rest read it."""
    first = {"input": 150, "cache_write": corpus_tokens, "cache_read": 0, "output": 0}
    rest = {"input": 150, "cache_write": 0, "cache_read": corpus_tokens, "output": 0}
    base = cost_usd(first, settings.prices) + (n - 1) * cost_usd(rest, settings.prices)
    per_token = settings.prices["output_per_mtok"] / 1_000_000
    return base + n * 600 * per_token, base + n * 1500 * per_token  # 600-1,500 output tokens per answer


def judge(q: dict, ans: Answer, qa: MeetingQA) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []
    text = ans.text.lower()
    results.append(("answered without an API error", not ans.error, ans.error))
    for needle in q.get("expect_all", []):
        results.append((f"mentions {needle!r}", needle.lower() in text, ""))
    if q.get("not_found"):
        results.append(("says it was not found", "not found in the transcripts" in text, ""))
    for want in q.get("expect_files", []):
        cited = [c.meeting.path.name.lower() for c in (ans.check.citations if ans.check else []) if c.meeting]
        results.append((f"cites a {want!r} meeting", any(want.lower() in name for name in cited), ""))
    if ans.check:
        bad = ans.check.counts["bad"] + sum(1 for x in ans.check.uncited_quotes if x.status == "missing")
        results.append(("no invented quotes or unknown citations", bad == 0, f"{bad} flagged"))
    return results


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--only", help="comma-separated question ids")
    parser.add_argument("--effort", help="override the effort for this run (low | medium | high)")
    parser.add_argument("--model", help="override the model for this run (it needs a price entry in default.yaml)")
    parser.add_argument("--transcripts", default=str(ROOT / "samples" / "transcripts"),
                        help="folder with the transcripts to ask about (default: the fictional samples)")
    parser.add_argument("--out", help="where to write the results (default: evals/results/quality-<model>-<time>.json)")
    args = parser.parse_args()

    try:
        settings = with_overrides(load_settings(), args.model, args.effort)
    except ConfigError as exc:
        print(f"Config problem: {exc}")
        return 2
    # An isolated log and no daily or monthly limit: the eval must neither read nor write the user's own usage history.
    settings = replace(settings, transcripts_dir=Path(args.transcripts), usage_log=RESULTS / "_quality-usage.csv",
                       max_daily_cost_usd=0, max_monthly_cost_usd=0)
    RESULTS.mkdir(exist_ok=True)
    loaded = load_meetings(settings.transcripts_dir, settings.meetings_meta)
    if not loaded.meetings:
        print("No transcripts found; nothing to evaluate.")
        return 2
    questions = yaml.safe_load((HERE / "questions.yaml").read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]

    qa = MeetingQA(settings, loaded.meetings, CostTracker(settings))
    tokens, how = qa.count_corpus_tokens()
    low, high = estimate(len(questions), tokens, settings)
    print(f"{len(questions)} questions on {settings.model} (effort {settings.effort}); prompt {tokens:,} tokens ({how}).")
    print(f"Estimated cost: ${low:.2f} to ${high:.2f}  (cap for this run: ${settings.max_session_cost_usd:.2f})")
    if args.dry_run:
        return 0
    if not has_credentials():
        print("No API key found. Save one in the app's Settings, set ANTHROPIC_API_KEY, or use --dry-run.")
        return 2
    if not args.yes and input("Run now? [y/N] ").strip().lower() != "y":
        return 1

    outcomes = []
    for q in questions:
        print("\n" + "=" * 78 + f"\n{q['id']}  ({q['kind']})\nQ: {q['question']}\n")
        ans = qa.ask(q["question"], [])
        print_answer(ans)
        checks = judge(q, ans, qa)
        for name, ok, detail in checks:
            print(f"   [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail and not ok else ""))
        outcomes.append({
            "id": q["id"], "kind": q.get("kind", ""), "passed": all(ok for _, ok, _ in checks),
            "cost": round(ans.cost, 6), "answer": ans.text, "error": ans.error,
            "citations": [c.status for c in (ans.check.citations if ans.check else [])],
            "uncited_missing": sum(1 for x in (ans.check.uncited_quotes if ans.check else []) if x.status == "missing"),
            "numbers": {"checked": len(ans.check.numbers) if ans.check else 0,
                        "flagged": len(ans.check.numbers_flagged) if ans.check else 0},
            "failed_checks": [name for name, ok, _ in checks if not ok],
        })

    passed = sum(o["passed"] for o in outcomes)
    tally = {s: sum(o["citations"].count(s) for o in outcomes) for s in ("ok", "weak", "bad")}
    print("\n" + "=" * 78 + f"\nPassed {passed}/{len(outcomes)}   total cost ${qa.tracker.total:.4f}   "
          f"citations: {tally['ok']} ok, {tally['weak']} weak (wrong turn or speaker), {tally['bad']} bad "
          f"[{settings.model}, effort {settings.effort}]")
    for o in outcomes:
        if not o["passed"]:
            print(f"  FAILED {o['id']}: {', '.join(o['failed_checks'])}")
    summary = {"suite": "quality", "model": settings.model, "effort": settings.effort,
               "run_at": datetime.now().isoformat(timespec="seconds"), "questions": len(outcomes), "passed": passed,
               "cost_usd": round(qa.tracker.total, 6), "outcomes": outcomes}
    RESULTS.mkdir(exist_ok=True)
    out = Path(args.out) if args.out else RESULTS / f"quality-{settings.model}-{datetime.now():%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Saved {out}")
    return 0 if passed == len(outcomes) else 1


if __name__ == "__main__":
    sys.exit(main())
