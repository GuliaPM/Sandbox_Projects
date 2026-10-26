"""TOK-01: real token counts against the app's estimate. Free (the count_tokens endpoint is not billed), needs a key.

    python evals/token_count.py                                   Haiku 5.5, cells S M L XL, RAG excerpts at XL
    python evals/token_count.py --models claude-haiku-5-5 claude-sonnet-5-5

For each transcript size it counts the full-context prompt exactly as the app sends it (rules + transcripts), and for
every question in the question file it counts the RAG prompt (rules + excerpts) at the RAG cell. Each count is compared
with the app's own estimate (transcripts.estimate_tokens) and with the old "characters / 4" rule. The excerpt tokens are
the RAG prompt minus the same prompt with no excerpts. Results go to evals/results/tokens-<time>.json; the summary is in
evals/TOKENS.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import corpus  # noqa: E402
import questionset  # noqa: E402
from meetminer.engine import (MeetingQA, build_system, build_system_rag, get_context, has_credentials, load_env,  # noqa: E402
                              load_settings)
from meetminer.retrieval import Retriever  # noqa: E402
from meetminer.transcripts import estimate_tokens, load_meetings  # noqa: E402

RESULTS = HERE / "results"


def count(qa: MeetingQA, model: str, system: list[dict]) -> int:
    """Input tokens of this system prompt plus the one-word message count_corpus_tokens also uses."""
    return qa.client.messages.count_tokens(model=model, system=system,
                                           messages=[{"role": "user", "content": "ping"}]).input_tokens


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--models", nargs="+", default=["claude-haiku-5-5"])
    parser.add_argument("--cells", nargs="+", default=["S", "M", "L", "XL"], choices=["S", "M", "L", "XL"])
    parser.add_argument("--rag-cell", default="XL", choices=["S", "M", "L", "XL"])
    parser.add_argument("--questions", default=str(questionset.ORIGINAL))
    args = parser.parse_args()
    if not has_credentials():
        print("No API key found (count_tokens is free but needs a key).")
        return 2
    settings = load_settings()
    questions = questionset.load(args.questions)
    out = {"suite": "TOK-01", "run_at": datetime.now().isoformat(timespec="seconds"), "models": {}}
    with tempfile.TemporaryDirectory() as tmp:
        folders = {c: corpus.build_cell(c, Path(tmp)) for c in set(args.cells) | {args.rag_cell}}
        for model in args.models:
            m = out["models"][model] = {"cells": {}, "rag": {}}
            for cell in args.cells:
                meetings = load_meetings(folders[cell]).meetings
                qa = MeetingQA(replace(settings, model=model, transcripts_dir=folders[cell]), meetings)
                system = build_system(get_context(meetings, "", []), settings.cache_ttl)
                text = system[0]["text"]
                actual = count(qa, model, system)
                m["cells"][cell] = {"meetings": len(meetings), "chars": len(text), "actual": actual,
                                    "estimate": estimate_tokens(text), "chars_div_4": len(text) // 4,
                                    "chars_per_token": round(len(text) / actual, 3)}
                print(f"{model}  {cell:<3} {len(meetings):>3} meetings  actual {actual:>8,}  estimate "
                      f"{estimate_tokens(text):>8,}  chars/4 {len(text) // 4:>8,}  {len(text) / actual:.2f} chars per token")
            meetings = load_meetings(folders[args.rag_cell]).meetings
            qa = MeetingQA(replace(settings, model=model, engine="rag", transcripts_dir=folders[args.rag_cell]), meetings)
            empty = count(qa, model, build_system_rag(""))
            retriever = Retriever(meetings, settings.rag_top_k, settings.rag_neighbors, settings.rag_max_tokens)
            asked: dict[str, tuple[str, str]] = {}
            for q in questions:
                got = retriever.retrieve(q["question"], questionset.history_for(q, asked))
                asked[q["id"]] = (q["question"], "")
                if not got.turns:
                    m["rag"][q["id"]] = {"no_match": True}
                    continue
                system = build_system_rag(got.text)
                actual = count(qa, model, system)
                m["rag"][q["id"]] = {"prompt_actual": actual, "excerpt_actual": actual - empty,
                                     "excerpt_chars": len(got.text), "retriever_estimate": got.tokens_estimate,
                                     "excerpt_chars_div_4": len(got.text) // 4, "budget": settings.rag_max_tokens}
                print(f"{model}  RAG {args.rag_cell} {q['id']:<20} prompt {actual:>6,}  excerpts {actual - empty:>6,} "
                      f"(retriever's estimate {got.tokens_estimate:>6,}, budget {settings.rag_max_tokens:,})")
            m["rag_rules_tokens"] = empty
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"tokens-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
