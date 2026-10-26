# Evals

Evals measure the real app against the real Claude API. They cost money (a few cents, see each one), so they are not part
of the unit tests; the unit tests in `../tests/` need no key and no network.

| Eval | What it answers | Run | Cost on Haiku |
|---|---|---|---|
| **Quality** (`run_eval.py`, `questions.yaml`) | Does it answer ten questions correctly, with citations that check out, and say "not found" when it should? | `python evals/run_eval.py` | about $0.002 |
| **OPS-01** (`ops_eval.py`, [OPS-01.md](OPS-01.md)) | How fast (p95), how often does it fail, and what does a correct answer cost, for 4k, 15k and 60k tokens of transcripts? | `python evals/ops_eval.py --cell S --reps 10` | about $0.02 to $0.1 per cell |
| **Injection probes** (`run_probes.py`, `probes/`, guardrail [GR-INJ-03](../guardrails/GR-INJ-03.md)) | Does it ignore instructions planted inside a transcript? | `python evals/run_probes.py` | about $0.002 |
| **RET-01 retrieval** (`retrieval_eval.py`) | Does the RAG engine's retrieval pick the turns that hold each answer, at 4k, 15k, 60k and 150k tokens, and how much less does it send? | `python evals/retrieval_eval.py` | free (no API) |

Add `--engine rag` to the quality eval, the probes and OPS-01 to run them against the RAG engine (results are saved as
`quality-rag-…`, `probes-rag-…`, `ops-rag-…`), `--dry-run` for the cost estimate without spending anything,
`--model claude-sonnet-5-5` to run another model, and `--yes` to skip the confirmation. OPS-01 has four transcript sizes:
`--cell S | M | L | XL` (about 4k, 15k, 60k and 150k tokens). They need an API key (saved in the app's Settings, or `ANTHROPIC_API_KEY`).
They use their own usage log in `results/` and ignore the app's spending limits, so they never touch your own history.

Results are saved in [`results/`](results/): one file per run. `show_flagged.py` explains every flagged citation of the
newest quality run. `../scorecards/build_scorecard.py` scores the newest result of each kind.

## Files

```
evals/
├── questions.yaml        the ten quality questions, with the facts each answer must contain
├── run_eval.py           quality eval (also provides the checks OPS-01 reuses)
├── show_flagged.py       prints each flagged citation with the closest text in the transcripts
├── OPS-01.md             the operational eval: definitions, protocol, targets, limits
├── ops_eval.py           OPS-01 runner
├── opsstats.py           OPS-01 statistics (p50/p95/p99, failure rate and its bound, cost per success)
├── corpus.py             deterministic synthetic transcripts that make the corpus 15k, 60k or 150k tokens
├── retrieval_eval.py     RET-01: retrieval recall for the RAG engine, free
├── run_probes.py         injection probes runner and judge
├── probes/               the planted-attack transcript and probes.yaml
└── results/              what the runs found (the files are small and fictional: committed on purpose)
```
