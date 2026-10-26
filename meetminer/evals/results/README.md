# Eval results

One file per run, written by the eval scripts in the folder above. They are kept in the repository as evidence: they only
contain questions, answers and numbers about the fictional sample transcripts.

| File | From | Content |
|---|---|---|
| `quality-<model>-<time>.json` | `run_eval.py` | each question's answer, citation tally, checks that failed, cost |
| `probes-<model>-<time>.json` | `run_probes.py` | each probe's answer and whether it resisted |
| `ops-<model>-<cell>-<time>.jsonl` | `ops_eval.py` | one line per attempt: latency, tokens, cost, failure kind, checks |
| `retrieval-<time>.json` | `retrieval_eval.py` | RET-01 per transcript size: answer turns retrieved, text sent, traps |
| `…-rag-…` | any of the above with `--engine rag` | the same, for the RAG engine |
| `…-heldout-…`, `…-hard-…`, `…-pre-audit-…` | `--questions heldout/questions.yaml`, `--distractors hard`, `--synonyms pre-audit` | runs on the held-out set, the near-miss filler, or the 1.2.0 synonym list ([CONTAMINATION.md](../CONTAMINATION.md)); the scorecard never mistakes them for the default runs |
| `superseded-...` | | a run kept for the record that a later change made out of date (see below) |
| `_*.csv` | | the runs' scratch usage logs (not committed) |

`superseded-probes-claude-haiku-5-5-20261025-003504-strict-judge.json` is the first live run of the injection probes. It
scored 9/10 because the judge flagged an answer that gave the right price and said it had ignored the planted "one dollar"
instruction: the words of the attack appeared in a sentence describing it. The judge was changed to tell reporting an attack
from obeying it (and a test keeps an obedient answer failing), and the probes were run again.

Two operational runs on the sample transcripts were also superseded, both Haiku, 100 attempts each:

- `superseded-ops-claude-haiku-5-5-S-20261025-003621-counts-only.jsonl` recorded only how many numbers were flagged, not
  which ones, so it could not be inspected. It was re-run with details.
- `superseded-ops-claude-haiku-5-5-S-20261025-004833-before-numbers-fix.jsonl` is that re-run. Reading the flagged numbers
  showed two bugs in the numbers check (GR-INT-04): a figure followed by a colon ("$12,000:") was cut to "$12", and two
  citations joined by "and" were not treated as one. Both were fixed, with tests, and the run was repeated. Nothing was
  selected for a better result: the newest run is always the one used.

The contamination audit (2026-10-26, [CONTAMINATION.md](../CONTAMINATION.md)) removed the synonyms that mirrored the eval
questions, which made these out of date:

- `superseded-retrieval-20261025-221401-pre-audit-synonyms.json` and `superseded-quality-rag-claude-haiku-5-5-20261025-221819-pre-audit-synonyms.json`:
  RET-01 and the RAG quality eval of 1.2.0, measured with the old synonym list.
- `superseded-retrieval-20261026-101900-pre-audit-synonyms-baseline.json`: the RET-01 baseline taken before any change
  (same numbers as `retrieval-pre-audit-20261026-102514.json`).
- `superseded-retrieval-*-20261026-1025*-paperwork-added.json` (four files): the first runs after the audit. The audited
  list had wrongly given `documents` a new alternative (`paperwork`) instead of only removing entries; a test caught it,
  it was taken out, and RET-01 was run again. The numbers were the same.

The token-count fix (2026-10-26, [TOKENS.md](../TOKENS.md)) made `rag_max_tokens` mean real tokens, so RAG now sends less
text per question. Everything measured on the RAG engine before it was re-run:

- `superseded-ops-rag-…-20261025-*-pre-audit-synonyms.jsonl` (S, M, L, XL) and
  `superseded-probes-rag-claude-haiku-5-5-20261025-221847-pre-audit-synonyms.json`: the 1.2.0 RAG runs (old synonyms,
  old budget).
- `superseded-quality-rag-…-20261026-10343*/103505-before-token-fix.json` and
  `superseded-retrieval-*-20261026-1032*-before-token-fix.json`: the runs after the synonym audit, before the token fix.

`quality-claude-haiku-5-5-20261026-103352.json` is a re-run of the full-context quality eval for the held-out comparison;
it is the newest and so the one used, and the earlier run stays as it was.

The scorecard builder uses the newest file of each kind for a model and ignores files that start with `superseded-`.
