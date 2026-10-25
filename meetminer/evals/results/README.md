# Eval results

One file per run, written by the eval scripts in the folder above. They are kept in the repository as evidence: they only
contain questions, answers and numbers about the fictional sample transcripts.

| File | From | Content |
|---|---|---|
| `quality-<model>-<time>.json` | `run_eval.py` | each question's answer, citation tally, checks that failed, cost |
| `probes-<model>-<time>.json` | `run_probes.py` | each probe's answer and whether it resisted |
| `ops-<model>-<cell>-<time>.jsonl` | `ops_eval.py` | one line per attempt: latency, tokens, cost, failure kind, checks |
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

The scorecard builder uses the newest file of each kind for a model and ignores files that start with `superseded-`.
