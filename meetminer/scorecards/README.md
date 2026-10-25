# Release scorecard

Before a release, MeetMiner is scored on five things: whether its answers are right, whether its citations hold up, how it
performs (speed, failures, cost), whether it is safe with keys and instructions, and whether the release itself is in order.
The scorecard turns that into one decision: **SHIP**, **REVIEW** or **HOLD**, and says which check caused it.

```
python scorecards/build_scorecard.py                 gather the evidence, score it, write latest.json and latest.md
python scorecards/build_scorecard.py --skip-sandbox  leave out the install test (it opens windows for a minute)
```

The latest result is [`latest.md`](latest.md) (readable) and `latest.json` (what the app's Reports page shows). Each run
is also kept in [`history/`](history/).

## How it is scored

- Every metric is **green** (2 points), **amber** (1) or **red** (0). A metric nobody measured is **not measured** and scores 0.
- A dimension's score is its points as a share of the best possible. The total weights the five dimensions
  (quality 35%, citations 20%, operations 20%, safety and privacy 15%, release hygiene 10%).
- Some metrics are **gates**. A red gate, or a gate with no evidence, means HOLD whatever the total.
- **SHIP** needs a total of 85 or more, no red gate and nothing unmeasured. **REVIEW** is a total of 70 or more with a gap
  (a missing measurement, or a total under 85). Anything lower, or any gate problem, is **HOLD**.
- Metrics marked "for information" (larger transcripts, other models) are shown but not scored.

What is measured and every threshold is in [`scorecard.spec.yaml`](scorecard.spec.yaml). Change a target there, in a commit
that says why, never in the code. The thresholds for speed, failures and cost are proposed starting points; the numbers they
are compared with are measured.

## Where the evidence comes from

| Evidence | From | Needs |
|---|---|---|
| Answer quality, citation integrity | `evals/results/quality-<model>-*.json` and the OPS-01 runs | `python evals/run_eval.py` |
| Speed, failures, cost per successful task | `evals/results/ops-<model>-<cell>-*.jsonl` | `python evals/ops_eval.py` ([OPS-01](../evals/OPS-01.md)) |
| Prompt-injection probes | `evals/results/probes-<model>-*.json` | `python evals/run_probes.py` |
| Unit tests, key and spending-limit tests | run by the builder | nothing |
| Release scan | the builder exports the publishable files and scans them (`packaging/export_public.py`) | nothing |
| Install, refused upgrade, upgrade, uninstall | `packaging/sandbox_test.ps1`, run by the builder | `python packaging/build.py` first |
| Dependency range and changelog | read from `pyproject.toml` and `CHANGELOG.md` | nothing |

The evals call the real API and cost a few cents, so the builder does not run them: run them first, then build the card.
The default configuration judged is Haiku on the fictional sample transcripts; other models appear in the notes.

## Reading it in the app

Settings, Reports shows the same card: the headline, what blocked it, a row per check with target, measured value and result,
and where the file came from. "Open report file" loads any scorecard JSON. The installed app carries the scorecard of its own
release (`release-scorecard.json`); a source checkout shows `scorecards/latest.json`.

## Blank scorecard

[`scorecard-template.md`](scorecard-template.md) is the same form to fill in by hand, for a change the tooling does not cover.
