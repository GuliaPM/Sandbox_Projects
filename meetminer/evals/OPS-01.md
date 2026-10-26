# OPS-01: operational performance eval

```text
EVAL ID:        OPS-01            VERSION: 1.0        OWNER: maintainer        FIRST RUN: 2026-10-25
PURPOSE:        Show that MeetMiner answers fast enough, rarely fails, and costs little per answer that is actually
                correct and verified.
QUESTION:       Are p95 latency, failure rate and cost per successful task inside target for each model and transcript
                size that is shipped or recommended?
RUNNER:         python evals/ops_eval.py        STATISTICS: evals/opsstats.py        SCORED BY: scorecards/

SYSTEM UNDER TEST
  The app's own engine (MeetingQA.ask), unchanged: the same prompt, effort "low", max_tokens 2000, cache_ttl 5m, request
  timeout 60 s with 2 retries. The eval swaps in no mock and bypasses none of the app's checks: every answer goes through
  the citation checker and the numbers check. The one thing it does differently is described under "Cold calls".

DATASET
  Questions   evals/questions.yaml: ten questions with known answers (lookups, commitments, a cross-meeting comparison,
              and two whose answer is not in the transcripts).
  Corpus      Latency and cost grow with the size of the transcripts, so each size is its own "cell":
                S   samples/transcripts, the two fictional calls (about 3,900 prompt tokens)
                M   S plus synthetic filler meetings, about 15,000 tokens (roughly two real one-hour calls)
                L   S plus synthetic filler, about 60,000 tokens (the size at which the app starts to warn)
                XL  S plus synthetic filler, about 160,000 tokens (some twenty one-hour calls)
  Engines     --engine full (default) or --engine rag. RAG sends different excerpts each time, so it has no prompt
              cache and its attempts are recorded as "uncached" instead of cold or warm. A RAG question that matches
              no passage is answered without a call (GR-RAG-01) and counts as an attempt at 0 s and $0.
              The filler (evals/corpus.py) is deterministic and avoids every fact the questions ask about.
  Size        A round asks every question once. Ten rounds is 100 attempts, the least that makes a p95 mean something
              (with 20 attempts the p95 is just the second-slowest one).

DEFINITIONS
  Attempt          one call to MeetingQA.ask. A retry inside the library is not counted separately.
  Call success     an answer came back, it ended normally, and there was no error.
  Task success     call success AND every check of the question passed (the facts are in the answer, the right meeting
                   is cited, no invented quote or unknown citation). Same checks as evals/run_eval.py.
  Failure          an attempt that is not a call success, unless the person could fix it themselves (a bad key, no
                   credit, a spending limit, no transcripts). Those are reported but kept out of the failure rate.
                   Kinds: timeout, network, rate_limit, api, refusal, truncation.
  Cold / warm      cold = the prompt cache has to be written; warm = it is read. Always reported separately.
  p50, p95, p99    nearest-rank percentiles of the API round trip (the engine's own `seconds`), over answers only.
  Failure rate     failures / (attempts - user-fixable failures), with the upper end of its 95% confidence interval.
                   With no failures in n attempts that bound is about 3/n, so "0%" on 100 attempts means "under 3.7%".
  Cost per success the cost of ALL attempts, failed and wrong ones included, divided by the number of task successes.

COLD CALLS
  Waiting five minutes for the cache to expire between rounds would make a run take hours. Instead each round puts its
  own random line in front of the transcripts, which makes that prompt new: its first question is a real cold call
  (cache written, full price) and the rest read the cache. The app itself never does this.

PROTOCOL
  1. One call at a time (the app is a single-person tool), a new process per run, nothing else running on the machine.
  2. python evals/ops_eval.py --cell S --reps 10 --dry-run   first: the estimate, no spend. The run stops by itself at
     --max-cost (default $2).
  3. Run Haiku (the default) and Sonnet separately; run S, M and L. Do not change the prompt or configuration mid-run.
  4. Every attempt, failed ones too, is appended to evals/results/ops-<model>-<cell>-<time>.jsonl as it finishes.

RESULT ROW (one JSON line per attempt)
  run_id, ts, model, effort, cell, corpus_tokens, round, question_id, kind, cache_state, cache_observed, ok, failure_kind,
  error, stop_reason, latency_s, total_s, tokens, cost_usd, task_success, failed_checks, citations, uncited_missing, numbers,
  request_id

TARGETS (PROPOSED starting points, the numbers are measured but the targets are choices; they live in
         scorecards/scorecard.spec.yaml)
  Metric                          Green               Amber            Red
  p95 latency, warm, cell S       <= 8 s              8-15 s           > 15 s
  p95 latency, cold, cell S       <= 15 s             15-25 s          > 25 s
  Failure rate (gate)             <= 1%               1-3%             > 3%
  Task success                    >= 98%              95-98%           < 95%
  Cost per successful task, S     Haiku <= $0.0005    up to 2x green   more
                                  Sonnet <= $0.006
  Cells M and L are measured and shown for information; they are not scored.

HOW TO READ THE RESULTS
  python evals/ops_eval.py --report evals/results/ops-*.jsonl
  python scorecards/build_scorecard.py        scores the newest results against the targets

KNOWN LIMITS
  - The transcripts are fictional and short, so the answers are short; real answers can be longer and slower.
  - The runs come from one machine and network at one time of day. Latency is dominated by the API, which varies.
  - The cold-call trick measures a cold cache; it does not measure the app's real idle-then-ask behaviour.
  - Cost uses the prices in config/default.yaml, not the invoice.
  - The filler is nonsense text, so M and L measure size, not comprehension of a messy real archive.
```
