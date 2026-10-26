"""OPS-01, the operational eval: p95 latency, failure rate and cost per successful task, against the real Claude API.

    python evals/ops_eval.py --cell S --reps 10 --dry-run     cost estimate only (no spend)
    python evals/ops_eval.py --cell S --reps 10 --yes         100 attempts on the sample transcripts (about $0.002 on Haiku)
    python evals/ops_eval.py --cell M --model claude-sonnet-5-5 --reps 5 --yes
    python evals/ops_eval.py --engine rag --cell XL --reps 3 --yes     the RAG engine on ~150k tokens of transcripts
    python evals/ops_eval.py --report evals/results/ops-*.jsonl     only print the statistics of earlier runs
    python evals/ops_eval.py --questions evals/heldout/questions.yaml --distractors hard --cell L --dry-run

Each repetition ("round") asks every question in the question file (default evals/questions.yaml) once; a question
with `follows: <id>` is asked with that question and its answer from the same round as the previous turn. The first question of a round is a cold
call (the prompt cache has to be written) and the rest are warm calls that read it. To get a cold call without waiting
five minutes for the cache to expire, each round puts its own random line in front of the transcripts, which makes the
prompt new; the app itself never does this. Every attempt, including failed ones, is written to a .jsonl file in
evals/results/ as soon as it finishes. See OPS-01.md for the definitions and the targets.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import replace
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))  # lets this run from a checkout without installing the package
sys.path.insert(0, str(HERE))

import corpus  # noqa: E402
import opsstats  # noqa: E402
import questionset  # noqa: E402
import run_eval  # noqa: E402
from meetminer import engine  # noqa: E402
from meetminer.engine import (  # noqa: E402
    ConfigError, CostTracker, MeetingQA, cost_usd, has_credentials, load_env, load_settings, with_overrides,
)
from meetminer.transcripts import load_meetings, render_corpus  # noqa: E402

RESULTS = HERE / "results"
ROUND = {"nonce": ""}  # the random line put in front of the transcripts for the current round


def _round_context(meetings, question, history) -> str:
    """engine.get_context, with the round's random line in front so that a new round starts with a cold cache."""
    return (f"[eval round {ROUND['nonce']}]\n" if ROUND["nonce"] else "") + render_corpus(meetings)


def estimate_rag(attempts: int, context_tokens: int, prices: dict) -> tuple[float, float]:
    """(low, high) USD for RAG: no cache, so every attempt pays the full input price for its excerpts."""
    each = cost_usd({"input": context_tokens + 150, "cache_write": 0, "cache_read": 0, "output": 0}, prices)
    per_token = prices["output_per_mtok"] / 1_000_000
    return attempts * (each + 600 * per_token), attempts * (each + 1500 * per_token)


def estimate(rounds: int, questions: int, corpus_tokens: int, prices: dict) -> tuple[float, float]:
    """(low, high) USD. Per round: one cold question (the cache is written) and the rest warm (it is read)."""
    cold = {"input": 150, "cache_write": corpus_tokens, "cache_read": 0, "output": 0}
    warm = {"input": 150, "cache_write": 0, "cache_read": corpus_tokens, "output": 0}
    base = rounds * (cost_usd(cold, prices) + (questions - 1) * cost_usd(warm, prices))
    per_token = prices["output_per_mtok"] / 1_000_000
    n = rounds * questions
    return base + n * 600 * per_token, base + n * 1500 * per_token


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cell", default="S", choices=["S", "M", "L", "XL"],
                        help="transcript size: S samples, M ~15k tokens, L ~60k, XL ~150k")
    parser.add_argument("--engine", choices=["full", "rag"], default="full", help="full context (default) or RAG")
    parser.add_argument("--reps", type=int, default=10, help="rounds; each asks every question once (default 10)")
    parser.add_argument("--model", help="override the model for this run")
    parser.add_argument("--effort", help="override the effort for this run")
    parser.add_argument("--only", help="comma-separated question ids")
    parser.add_argument("--questions", default=str(questionset.ORIGINAL),
                        help="question file (default: evals/questions.yaml; held-out: evals/heldout/questions.yaml)")
    parser.add_argument("--distractors", choices=corpus.DISTRACTORS, default="easy",
                        help="filler for M/L/XL: easy (default, the original) or hard (near-miss facts)")
    parser.add_argument("--no-cold", action="store_true", help="do not force a cold call at the start of each round")
    parser.add_argument("--max-cost", type=float, default=2.0, help="stop when this run has cost this much (default $2)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--out", help="results file (default: evals/results/ops-<model>-<cell>-<time>.jsonl)")
    parser.add_argument("--report", nargs="+", metavar="FILE", help="only print the statistics of these .jsonl files")
    args = parser.parse_args()

    if args.report:
        print(opsstats.report(opsstats.summarize(opsstats.load_rows([Path(p) for p in args.report]))))
        return 0

    try:
        settings = with_overrides(load_settings(), args.model, args.effort, args.engine)
    except ConfigError as exc:
        print(f"Config problem: {exc}")
        return 2
    RESULTS.mkdir(exist_ok=True)
    # An isolated log, and none of the app's own limits: this run must neither read nor write the user's usage history.
    settings = replace(settings, usage_log=RESULTS / "_ops-usage.csv", max_daily_cost_usd=0, max_monthly_cost_usd=0,
                       max_session_cost_usd=1e9)
    questions = questionset.only(questionset.load(args.questions), args.only)
    qset = questionset.label(args.questions)

    def measure(folder: Path) -> int:
        probe = MeetingQA(replace(settings, transcripts_dir=folder), load_meetings(folder).meetings)
        return probe.count_corpus_tokens()[0]

    folder = corpus.build_cell(args.cell, Path(__file__).resolve().parent / "_cache", measure, args.distractors)
    settings = replace(settings, transcripts_dir=folder)
    loaded = load_meetings(folder)
    qa = MeetingQA(settings, loaded.meetings, CostTracker(settings))
    tokens, how = qa.count_corpus_tokens()
    if settings.engine == "rag":  # no cache: every question pays for its excerpts (at most rag_max_tokens) and the rules
        low, high = estimate_rag(args.reps * len(questions), min(tokens, settings.rag_max_tokens + 900), settings.prices)
    else:
        low, high = estimate(args.reps, len(questions), tokens, settings.prices)
    attempts = args.reps * len(questions)
    print(f"OPS-01: {attempts} attempts ({args.reps} rounds x {len(questions)} {qset} questions, {args.distractors} "
          f"distractors) on {settings.model} "
          f"(effort {settings.effort}), engine {settings.engine}, cell {args.cell}: {len(loaded.meetings)} meetings, "
          f"{tokens:,} prompt tokens with full context ({how}).")
    print(f"Estimated cost: ${low:.4f} to ${high:.4f}   (this run stops at ${args.max_cost:.2f})")
    if args.dry_run:
        return 0
    if high > args.max_cost:
        print("The estimate is above --max-cost; raise it or lower --reps.")
        return 2
    if not has_credentials():
        print("No API key found. Save one in the app's Settings, set ANTHROPIC_API_KEY, or use --dry-run.")
        return 2
    if not args.yes and input("Run now? [y/N] ").strip().lower() != "y":
        return 1

    engine.get_context = _round_context  # the app's own prompt builder, with the cold-call line added
    run_id = uuid.uuid4().hex[:8]
    kind = "ops-rag" if settings.engine == "rag" else "ops"  # full context keeps the original file names
    extra = "-".join(p for p in (qset if qset != "original" else "", "hard" if args.distractors == "hard" else "") if p)
    kind += f"-{extra}" if extra else ""  # e.g. ops-rag-heldout-hard-<model>-...: never mistaken for the default runs
    out = Path(args.out) if args.out else RESULTS / f"{kind}-{settings.model}-{args.cell}-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    rows: list[dict] = []
    stopped = False
    with out.open("w", encoding="utf-8") as sink:
        for rnd in range(1, args.reps + 1):
            ROUND["nonce"] = "" if args.no_cold and rnd > 1 else uuid.uuid4().hex[:8]
            asked: dict[str, tuple[str, str]] = {}
            for idx, q in enumerate(questions):
                if qa.tracker.total >= args.max_cost:
                    stopped = True
                    break
                intended = "cold" if idx == 0 and not (args.no_cold and rnd > 1) else "warm"
                if settings.engine == "rag":
                    intended = "uncached"  # RAG sends different excerpts each time, so there is no cache to be warm
                started = time.perf_counter()
                ans = qa.ask(q["question"], questionset.history_for(q, asked))
                wall = time.perf_counter() - started
                asked[q["id"]] = (q["question"], ans.text)
                checks = run_eval.judge(q, ans, qa)
                ok = not ans.error
                row = {
                    "run_id": run_id, "ts": datetime.now().isoformat(timespec="seconds"), "model": settings.model,
                    "engine": settings.engine,
                    "effort": settings.effort, "cell": args.cell, "corpus_tokens": tokens, "round": rnd,
                    "questions_set": qset, "distractors": args.distractors,
                    "question_id": q["id"], "kind": q.get("kind", ""), "cache_state": intended,
                    "cache_observed": "warm" if ans.tokens["cache_read"] > 0 else "cold", "ok": ok,
                    "failure_kind": ans.error_kind if ans.error else "", "error": ans.error,
                    "stop_reason": ans.stop_reason, "latency_s": round(ans.seconds, 3), "total_s": round(wall, 3),
                    "tokens": ans.tokens, "cost_usd": round(ans.cost, 6),
                    "task_success": ok and all(passed for _, passed, _ in checks),
                    "failed_checks": [name for name, passed, _ in checks if not passed],
                    "citations": ans.check.counts if ans.check else {"ok": 0, "weak": 0, "bad": 0},
                    "uncited_missing": sum(1 for x in (ans.check.uncited_quotes if ans.check else []) if x.status == "missing"),
                    "numbers": {"checked": len(ans.check.numbers) if ans.check else 0,
                                "flagged": len(ans.check.numbers_flagged) if ans.check else 0},
                    "request_id": ans.request_id,
                    "retrieval": None if ans.retrieved is None else {
                        "turns": len(ans.retrieved.turns), "meetings": len(ans.retrieved.meetings),
                        "tokens_estimate": ans.retrieved.tokens_estimate,
                        "answer_turns_found": [ans.retrieved.holds(*t.split("|")) for t in q.get("answer_turns", [])]},
                }
                if ans.check and (ans.check.numbers_flagged or ans.check.counts["bad"] or ans.check.counts["weak"]) \
                        or not row["task_success"]:  # keep the evidence for anything that looks wrong
                    row["flagged_numbers"] = [{"value": f.value, "status": f.status, "where": f.where}
                                              for f in (ans.check.numbers_flagged if ans.check else [])]
                    row["answer"] = ans.text[:1500]
                rows.append(row)
                sink.write(json.dumps(row) + "\n")
                sink.flush()
                print(f"[round {rnd}/{args.reps}] {q['id']:<18}{intended:<5} {'ok' if ok else 'FAILED ' + ans.error_kind:<14}"
                      f"{ans.seconds:6.2f} s  ${ans.cost:.5f}  task {'PASS' if row['task_success'] else 'FAIL'}")
            if stopped:
                print(f"Stopped early: this run reached its ${args.max_cost:.2f} limit.")
                break
    print("\n" + opsstats.report(opsstats.summarize(rows)))
    print(f"\nSpent ${qa.tracker.total:.4f}.  Saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
