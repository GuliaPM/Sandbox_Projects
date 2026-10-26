# MeetMiner 1.2.0 release scorecard: RAG engine

**Decision: SHIP**  ·  score 100/100  ·  measured 2026-10-25  ·  configuration: claude-haiku-5-5, RAG, cell S (fictional samples (samples/transcripts))

## Answer quality  (100/100, weight 30%)

| Check | Target | Measured | Result |
|---|---|---|---|
| Correct answers to the sample questions **[gate]** | ≥ 100% (amber ≥ 90%) | 7/7 (100.0%) | good |
| Questions not in the transcripts handled **[gate]** | ≥ 100% (amber ≥ 50%) | 2/2 (100.0%) | good |
| Cross-meeting comparison correct | ≥ 100% (amber ≥ 50%) | 1/1 (100.0%) | good |

## Retrieval (RET-01)  (100/100, weight 15%)

| Check | Target | Measured | Result |
|---|---|---|---|
| Answer turns retrieved, sample transcripts **[gate]** | ≥ 100% (amber ≥ 87.5%) | 8/8 (100.0%) (2 meetings) | good |
| Answer turns retrieved, 60k-token transcripts | ≥ 100% (amber ≥ 87.5%) | 8/8 (100.0%) (39 meetings) | good |
| Answer turns retrieved, 160k-token transcripts | ≥ 100% (amber ≥ 87.5%) | 8/8 (100.0%) (99 meetings) | good |
| Text sent, share of full context, 160k-token transcripts | for information | 2% (about 2,912 of 114,328 tokens) | info |
| Not-in-the-transcripts questions ended without a call | for information | 1/2 (50.0%) ended without a call | info |

## Citation integrity  (100/100, weight 15%)

| Check | Target | Measured | Result |
|---|---|---|---|
| Citations verified by the checker **[gate]** | ≥ 98% (amber ≥ 95%) | 288/288 (100.0%) of 110 answers | good |
| Answers with an invented quote or unknown citation | ≤ 1% (amber ≤ 3%) | 0/110 (0.0%) | good |
| Citations pointing at the wrong turn | ≤ 2% (amber ≤ 5%) | 0/288 (0.0%) of citations | good |
| Answers with a number not in its cited turn (GR-INT-04) | ≤ 2% (amber ≤ 5%) | 1/110 (0.9%) | good |

## Operational performance  (100/100, weight 20%)

| Check | Target | Measured | Result |
|---|---|---|---|
| p95 latency (RAG has no cache, so no warm/cold split) | ≤ 8 s (amber ≤ 15 s) | 2.22 s | good |
| Failure rate (outside the person's control) **[gate]** | ≤ 1% (amber ≤ 3%) | 0.0% of 100 (95% upper bound 3.7%) | good |
| Tasks done right (answered, correct, cited) | ≥ 98% (amber ≥ 95%) | 99/100 (99.0%) | good |
| Cost per successful task | ≤ $0.001 (amber ≤ $0.002) | $0.00036 | good |
| p95 latency, 15k-token transcripts | for information | 3.51 s | info |
| p95 latency, 60k-token transcripts | for information | 3.46 s | info |
| p95 latency, 160k-token transcripts | for information | 1.85 s | info |
| Cost per successful task, 160k-token transcripts | for information | $0.00047 | info |
| Tasks done right, 160k-token transcripts | for information | 30/30 (100.0%) | info |

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
| Unit tests passing **[gate]** | ≥ 100% | 242/242 (100.0%) | good |
| Guardrail tests passing | ≥ 100% | 50/50 (100.0%) | good |
| Install, upgrade and uninstall pass in a sandbox | yes | pass | good |
| Dependency range bounded and changelog updated | yes | yes | good |

## Evidence

- Operational runs: evals/results/ops-rag-claude-haiku-5-5-S-20261025-221850.jsonl, evals/results/ops-rag-claude-haiku-5-5-M-20261025-222117.jsonl, evals/results/ops-rag-claude-haiku-5-5-L-20261025-222351.jsonl, evals/results/ops-rag-claude-haiku-5-5-XL-20261025-222511.jsonl
- Quality eval: evals/results/quality-rag-claude-haiku-5-5-20261025-221819.json
- Retrieval eval: evals/results/retrieval-20261025-221401.json
- Injection probes: evals/results/probes-rag-claude-haiku-5-5-20261025-221847.json
- Unit tests: 242/242 ok
- Release scan: CLEAN: no API key, no personal names or paths, no transcripts, no logs, no settings files.
- Install test: SANDBOX RESULT: PASS (14/14 checks)

## Notes

- The operational targets are proposed starting points (evals/OPS-01.md); the numbers are measured, the targets are choices.
- RAG answers from retrieved excerpts, so its accuracy depends on retrieval as well as on the model. RET-01 checks retrieval without any API call.

Guardrails exercised: GR-COST-05, GR-COST-06, GR-INT-04, GR-INJ-03, GR-REL-03, GR-RAG-01

Sign-off: ______________________   Date: ____________   Decision: SHIP / SHIP WITH WAIVERS / HOLD
