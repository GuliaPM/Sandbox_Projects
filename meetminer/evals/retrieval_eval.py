"""RET-01, the retrieval eval for the RAG engine. Free: no API key, no network, no cost.

    python evals/retrieval_eval.py                  all four transcript sizes (S, M, L, XL)
    python evals/retrieval_eval.py --cells S XL
    python evals/retrieval_eval.py --questions evals/heldout/questions.yaml --distractors hard
    python evals/retrieval_eval.py --synonyms pre-audit     the synonym list as shipped in 1.2.0 (for CONTAMINATION.md)

Every answerable question lists the turns that hold its answer (`answer_turns`). For each transcript size this measures
whether RAG's retrieval puts those turns in front of the model, because an answer the model never sees cannot be right.
It also measures how much less text RAG sends than full context, and whether the questions whose answer is not in the
transcripts end without a call at all. Results go to evals/results/retrieval-<time>.json for the default run (original
questions, easy distractors, current synonyms) and to retrieval-<variant>-<time>.json for any other.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import corpus  # noqa: E402
import questionset  # noqa: E402
from meetminer import retrieval  # noqa: E402
from meetminer.engine import DEFAULTS, build_system, build_system_rag  # noqa: E402
from meetminer.retrieval import Retriever  # noqa: E402
from meetminer.transcripts import CHARS_PER_TOKEN, estimate_tokens, load_meetings, render_corpus  # noqa: E402

RESULTS = HERE / "results"

# retrieval.SYNONYMS as shipped in 1.2.0 (commit 4fd6ef7), before the contamination audit removed the entries derived
# from the eval questions (evals/CONTAMINATION.md). Kept only so the "before" numbers can be reproduced.
PRE_AUDIT_SYNONYMS = {
    "price": ["cost", "pricing", "fee", "per"], "cost": ["price", "pricing", "spend"], "fee": ["price", "cost"],
    "document": ["report", "agreement", "contract", "paperwork"], "documents": ["report", "agreement", "contract"],
    "deadline": ["due", "date"], "due": ["deadline", "date"], "when": ["date", "day"],
    "budget": ["money", "spend", "dollars"], "money": ["budget", "dollars"],
    "action": ["task", "follow"], "owner": ["own", "responsible"], "owns": ["own", "owner", "deliver"],
    "decided": ["decision", "agreed", "agree"], "decision": ["decided", "agreed"],
    "security": ["soc", "compliance", "review"], "launch": ["release", "ship", "live"],
    "customers": ["users", "clients"], "invitations": ["invites", "invite"], "signing": ["sign", "signature"],
    "discount": ["prepay", "off"], "meeting": ["call"], "call": ["meeting"],
}
SYNONYM_SETS = {"current": None, "pre-audit": PRE_AUDIT_SYNONYMS}


def synonyms_fingerprint(synonyms: dict) -> str:
    return hashlib.sha256(json.dumps(synonyms, sort_keys=True).encode()).hexdigest()[:12]


def evaluate_cell(cell: str, questions: list[dict], workdir: Path, distractors: str = "easy") -> dict:
    meetings = load_meetings(corpus.build_cell(cell, workdir, distractors=distractors)).meetings
    # Estimates of the prompts exactly as the engines build them (transcripts.estimate_tokens, calibrated against the
    # count_tokens endpoint; evals/token_count.py measures the real counts).
    full_tokens = estimate_tokens(build_system(render_corpus(meetings), "5m")[0]["text"])
    started = time.perf_counter()
    retriever = Retriever(meetings, DEFAULTS["rag_top_k"], DEFAULTS["rag_neighbors"], DEFAULTS["rag_max_tokens"])
    build_ms = (time.perf_counter() - started) * 1000
    per_question, sent, ms = [], [], []
    asked: dict[str, tuple[str, str]] = {}
    for q in questions:
        history = questionset.history_for(q, asked)
        started = time.perf_counter()
        got = retriever.retrieve(q["question"], history)
        ms.append((time.perf_counter() - started) * 1000)
        asked[q["id"]] = (q["question"], "")
        gold = [tuple(t.split("|")) for t in q.get("answer_turns", [])]
        found = [got.holds(*g) for g in gold]
        if got.turns:
            sent.append(estimate_tokens(build_system_rag(got.text)[0]["text"]))
        per_question.append({"id": q["id"], "kind": q.get("kind", ""), "follows": q.get("follows"),
                             "turns": len(got.turns), "meetings": len(got.meetings),
                             "tokens_estimate": got.tokens_estimate, "answer_turns": len(gold),
                             "answer_turns_found": found, "no_match": not got.turns})
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


def variant(questions_set: str, distractors: str, synonyms: str) -> str:
    """'' for the default run, otherwise the parts that differ, for the file name: 'heldout-hard', 'pre-audit'..."""
    parts = [p for p, default in ((questions_set, "original"), (distractors, "easy"), (synonyms, "current")) if p != default]
    return "-".join(parts)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cells", nargs="+", default=["S", "M", "L", "XL"], choices=["S", "M", "L", "XL"])
    parser.add_argument("--questions", default=str(questionset.ORIGINAL),
                        help="question file (default: evals/questions.yaml; held-out: evals/heldout/questions.yaml)")
    parser.add_argument("--distractors", choices=corpus.DISTRACTORS, default="easy",
                        help="filler for M/L/XL: easy (default, the original) or hard (near-miss facts)")
    parser.add_argument("--synonyms", choices=list(SYNONYM_SETS), default="current",
                        help="current (default) or pre-audit (the 1.2.0 list, to reproduce the 'before' numbers)")
    parser.add_argument("--out", help="results file (default: evals/results/retrieval[-variant]-<time>.json)")
    args = parser.parse_args()
    questions = questionset.load(args.questions)
    qset = questionset.label(args.questions)
    synonyms = dict(SYNONYM_SETS[args.synonyms] or retrieval.SYNONYMS)  # a copy: patch.dict clears the target first
    cells = {}
    with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(retrieval.SYNONYMS, synonyms, clear=True):
        for cell in args.cells:
            cells[cell] = evaluate_cell(cell, questions, Path(tmp), args.distractors)
    print(f"questions: {qset}, distractors: {args.distractors}, synonyms: {args.synonyms}")
    print(f"{'cell':<5}{'meetings':>9}{'full ctx':>10}{'RAG sends':>11}{'share':>8}{'answers found':>15}{'recall':>8}"
          f"{'traps free':>12}{'ms':>7}")
    for cell, r in cells.items():
        print(f"{cell:<5}{r['meetings']:>9}{r['full_context_tokens_est']:>10,}{r['avg_tokens_sent_est']:>11,}"
              f"{r['share_of_full_context'] * 100:>7.0f}%{r['all_answer_turns_found']:>9}/{r['questions']:<5}"
              f"{r['answer_turn_recall'] * 100:>7.0f}%{r['traps_without_a_call']:>8}/{r['traps']:<3}{r['avg_retrieval_ms']:>7.1f}")
    RESULTS.mkdir(exist_ok=True)
    tag = variant(qset, args.distractors, args.synonyms)
    out = Path(args.out) if args.out else RESULTS / f"retrieval-{tag + '-' if tag else ''}{datetime.now():%Y%m%d-%H%M%S}.json"
    summary = {"suite": "RET-01", "engine": "rag", "run_at": datetime.now().isoformat(timespec="seconds"),
               "questions_set": qset, "questions_file": Path(args.questions).resolve().relative_to(ROOT).as_posix()
               if Path(args.questions).resolve().is_relative_to(ROOT) else str(args.questions),
               "distractors": args.distractors, "synonyms": args.synonyms, "chars_per_token": CHARS_PER_TOKEN,
               "synonyms_fingerprint": synonyms_fingerprint(synonyms),
               "settings": {k: DEFAULTS[k] for k in ("rag_top_k", "rag_neighbors", "rag_max_tokens")}, "cells": cells}
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Saved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
