# MeetMiner 1.2.0 release scorecard: RAG engine

**Decision: SHIP**  ·  score 98/100  ·  measured 2026-10-26  ·  configuration: claude-haiku-5-5, RAG, cell S (fictional samples (samples/transcripts))

## Answer quality  (100/100, weight 30%)

| Check | Target | Measured | Result |
|---|---|---|---|
| Correct answers to the sample questions **[gate]** | ≥ 100% (amber ≥ 90%) | 7/7 (100.0%) | good |
| Questions not in the transcripts handled **[gate]** | ≥ 100% (amber ≥ 50%) | 2/2 (100.0%) | good |
| Cross-meeting comparison correct | ≥ 100% (amber ≥ 50%) | 1/1 (100.0%) | good |
| Held-out questions answered correctly (model-written set) | for information | 9/12 (75.0%) | info |

## Retrieval (RET-01)  (100/100, weight 15%)

| Check | Target | Measured | Result |
|---|---|---|---|
| Answer turns retrieved, sample transcripts **[gate]** | ≥ 100% (amber ≥ 87.5%) | 8/8 (100.0%) (2 meetings) | good |
| Answer turns retrieved, L transcripts (about 65,000 tokens, 39 meetings) | ≥ 100% (amber ≥ 87.5%) | 8/8 (100.0%) (39 meetings) | good |
| Answer turns retrieved, XL transcripts (about 160,000 tokens, 99 meetings) | ≥ 100% (amber ≥ 87.5%) | 8/8 (100.0%) (99 meetings) | good |
| Text sent, share of full context, XL transcripts (about 160,000 tokens, 99 meetings) | for information | 3% (about 4,613 of 169,386 tokens) | info |
| Not-in-the-transcripts questions ended without a call | for information | 1/2 (50.0%) ended without a call | info |
| Held-out answer turns retrieved, sample transcripts | for information | 7/10 (70.0%) (2 meetings) | info |
| Held-out answer turns retrieved, XL transcripts with near-miss filler (99 meetings) | for information | 6/10 (60.0%) (99 meetings, hard distractors) | info |

## Citation integrity  (100/100, weight 15%)

| Check | Target | Measured | Result |
|---|---|---|---|
| Citations verified by the checker **[gate]** | ≥ 98% (amber ≥ 95%) | 261/262 (99.6%) of 110 answers | good |
| Answers with an invented quote or unknown citation | ≤ 1% (amber ≤ 3%) | 0/110 (0.0%) | good |
| Citations pointing at the wrong turn | ≤ 2% (amber ≤ 5%) | 1/262 (0.4%) of citations | good |
| Answers with a number not in its cited turn (GR-INT-04) | ≤ 2% (amber ≤ 5%) | 2/110 (1.8%) | good |

## Operational performance  (88/100, weight 20%)

| Check | Target | Measured | Result |
|---|---|---|---|
| p95 latency (RAG has no cache, so no warm/cold split) | ≤ 8 s (amber ≤ 15 s) | 2.90 s | good |
| Failure rate (outside the person's control) **[gate]** | ≤ 1% (amber ≤ 3%) | 0.0% of 100 (95% upper bound 3.7%) | good |
| Tasks done right (answered, correct, cited) | ≥ 98% (amber ≥ 95%) | 97/100 (97.0%) | watch |
| Cost per successful task | ≤ $0.001 (amber ≤ $0.002) | $0.00036 | good |
| p95 latency, M transcripts (about 15,000 tokens, 9 meetings) | for information | 3.83 s | info |
| p95 latency, L transcripts (about 65,000 tokens, 39 meetings) | for information | 3.52 s | info |
| p95 latency, XL transcripts (about 160,000 tokens, 99 meetings) | for information | 2.60 s | info |
| Cost per successful task, XL transcripts (about 160,000 tokens, 99 meetings) | for information | $0.00049 | info |
| Tasks done right, XL transcripts (about 160,000 tokens, 99 meetings) | for information | 30/30 (100.0%) | info |

## Safety and privacy  (100/100, weight 10%)

| Check | Target | Measured | Result |
|---|---|---|---|
| Release scan finds nothing private **[gate]** | yes | clean | good |
| API key stored encrypted and never shown in full **[gate]** | yes | yes | good |
| Prompt-injection probes that failed (GR-INJ-03) **[gate]** | ≤ 0 (amber ≤ 1) | 10/10 resisted | good |
| Index stays in memory; no match means no call (GR-RAG-01) | yes | yes | good |

## Release hygiene  (100/100, weight 10%)

| Check | Target | Measured | Result |
|---|---|---|---|
| Unit tests passing **[gate]** | ≥ 100% | 271/271 (100.0%) | good |
| Guardrail tests passing | ≥ 100% | 50/50 (100.0%) | good |
| Install, upgrade and uninstall pass in a sandbox | yes | pass | good |
| Dependency range bounded and changelog updated | yes | yes | good |

## What this scorecard does and does not show

- The 10 original questions (evals/questions.yaml) are a regression baseline on a development set: the retriever was built while looking at them, so a good score on them shows that nothing regressed, not that the app generalises.
- The held-out rows (evals/heldout/questions.yaml) ask paraphrased questions that nothing was tuned on. That set was written by a model (Claude Code), not by a person, and is about the same two fictional calls, so a human-written held-out set on real transcripts is still the open next step. Its rows are information, not gates.
- n is small: 10 original and 12 held-out questions, two short fictional calls (the larger sizes add synthetic filler meetings), and 30 to 100 attempts per operational row, so one question moves a percentage by 8 to 12 points.

## Evidence

- Operational runs: evals/results/ops-rag-claude-haiku-5-5-S-20261026-105116.jsonl, evals/results/ops-rag-claude-haiku-5-5-M-20261026-105402.jsonl, evals/results/ops-rag-claude-haiku-5-5-L-20261026-105646.jsonl, evals/results/ops-rag-claude-haiku-5-5-XL-20261026-105811.jsonl
- Quality eval: evals/results/quality-rag-claude-haiku-5-5-20261026-105014.json
- Retrieval eval: evals/results/retrieval-20261026-104853.json
- Held-out set (information only): evals/results/quality-rag-heldout-claude-haiku-5-5-20261026-105041.json, evals/results/retrieval-heldout-20261026-104901.json, evals/results/retrieval-heldout-hard-20261026-104904.json
- Injection probes: evals/results/probes-rag-claude-haiku-5-5-20261026-105113.json
- Unit tests: 271/271 ok
- Release scan: CLEAN: no API key, no personal names or paths, no transcripts, no logs, no settings files.
- Install test: SANDBOX RESULT: PASS (14/14 checks)

## Notes

- The operational targets are proposed starting points (evals/OPS-01.md); the numbers are measured, the targets are choices.
- RAG answers from retrieved excerpts, so its accuracy depends on retrieval as well as on the model. RET-01 checks retrieval without any API call.

Guardrails exercised: GR-COST-05, GR-COST-06, GR-INT-04, GR-INJ-03, GR-REL-03, GR-RAG-01

Sign-off: ______________________   Date: ____________   Decision: SHIP / SHIP WITH WAIVERS / HOLD
