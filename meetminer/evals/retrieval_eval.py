"""RET-01, the retrieval eval for the RAG engine. Free: no API key, no network, no cost.

    python evals/retrieval_eval.py                  all four transcript sizes (S, M, L, XL)
    python evals/retrieval_eval.py --cells S XL

Every answerable question in questions.yaml lists the turns that hold its answer (`answer_turns`). For each transcript
size this measures whether RAG's retrieval puts those turns in front of the model, because an answer the model never
sees cannot be right. It also measures how much less text RAG sends than full context, and whether the questions whose
answer is not in the transcripts end without a call at all. Results go to evals/results/retrieval-<time>.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import corpus  # noqa: E402
from meetminer.engine import DEFAULTS, SYSTEM_RULES  # noqa: E402
from meetminer.retrieval import Retriever  # noqa: E402
from meetminer.transcripts import load_meetings, render_corpus  # noqa: E402

RESULTS = HERE / "results"


def evaluate_cell(cell: str, questions: list[dict], workdir: Path) -> dict:
    meetings = load_meetings(corpus.build_cell(cell, workdir)).meetings
    full_tokens = (len(SYSTEM_RULES) + len(render_corpus(meetings))) // 4  # estimate: about 4 characters per token
    started = time.perf_counter()
    retriever = Retriever(meetings, DEFAULTS["rag_top_k"], DEFAULTS["rag_neighbors"], DEFAULTS["rag_max_tokens"])
    build_ms = (time.perf_counter() - started) * 1000
    per_question, sent, ms = [], [], []
    for q in questions:
        started = time.perf_counter()
        got = retriever.retrieve(q["question"])
        ms.append((time.perf_counter() - started) * 1000)
        gold = [tuple(t.split("|")) for t in q.get("answer_turns", [])]
        found = [got.holds(*g) for g in gold]
        if got.turns:
            sent.append(got.tokens_estimate + len(SYSTEM_RULES) // 4)
        per_question.append({"id": q["id"], "kind": q.get("kind", ""), "turns": len(got.turns),
                             "meetings": len(got.meetings), "tokens_estimate": got.tokens_estimate,
                             "answer_turns": len(gold), "answer_turns_found": found, "no_match": not got.turns})
    answerable = [p for p in per_question if p["answer_turns"]]
    traps = [p for p in per_question if p["kind"] == "not_in_data"]
    avg_sent = sum(sent) / len(sent) if sent else 0
    return {
        "meetings": len(meetings), "chunks": len(retriever.index.chunks), "full_context_tokens_est": full_tokens,
        "questions": len(answerable),
        "all_answer_turns_found": sum(all(p["answer_turns_found"]) for p in answerable),
        "any_answer_turn_found": sum(any(p["answer_turns_found"]) for p in answerable),
        "answer_turn_recall": (sum(sum(p["answer_turns_found"]) for p in answerable)
                               / max(sum(p["answer_turns"] for p in answerable), 1)),
        "avg_tokens_sent_est": round(avg_sent), "max_tokens_sent_est": max(sent, default=0),
        "share_of_full_context": round(avg_sent / full_tokens, 3) if full_tokens else None,
        "traps": len(traps), "traps_without_a_call": sum(p["no_match"] for p in traps),
        "index_build_ms": round(build_ms, 1), "avg_retrieval_ms": round(sum(ms) / len(ms), 2),
        "per_question": per_question,
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cells", nargs="+", default=["S", "M", "L", "XL"], choices=["S", "M", "L", "XL"])
    parser.add_argument("--out", help="results file (default: evals/results/retrieval-<time>.json)")
    args = parser.parse_args()
    questions = yaml.safe_load((HERE / "questions.yaml").read_text(encoding="utf-8"))
    cells = {}
    with tempfile.TemporaryDirectory() as tmp:
        for cell in args.cells:
            cells[cell] = evaluate_cell(cell, questions, Path(tmp))
    print(f"{'cell':<5}{'meetings':>9}{'full ctx':>10}{'RAG sends':>11}{'share':>8}{'answers found':>15}{'recall':>8}"
          f"{'traps free':>12}{'ms':>7}")
    for cell, r in cells.items():
        print(f"{cell:<5}{r['meetings']:>9}{r['full_context_tokens_est']:>10,}{r['avg_tokens_sent_est']:>11,}"
              f"{r['share_of_full_context'] * 100:>7.0f}%{r['all_answer_turns_found']:>9}/{r['questions']:<5}"
              f"{r['answer_turn_recall'] * 100:>7.0f}%{r['traps_without_a_call']:>8}/{r['traps']:<3}{r['avg_retrieval_ms']:>7.1f}")
    RESULTS.mkdir(exist_ok=True)
    out = Path(args.out) if args.out else RESULTS / f"retrieval-{datetime.now():%Y%m%d-%H%M%S}.json"
    summary = {"suite": "RET-01", "engine": "rag", "run_at": datetime.now().isoformat(timespec="seconds"),
               "settings": {k: DEFAULTS[k] for k in ("rag_top_k", "rag_neighbors", "rag_max_tokens")}, "cells": cells}
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
