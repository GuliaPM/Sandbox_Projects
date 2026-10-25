# MeetMiner 1.1.0 release scorecard

**Decision: SHIP**  ·  score 100/100  ·  measured 2026-10-25  ·  configuration: claude-haiku-5-5, cell S (fictional samples (samples/transcripts))

## Answer quality  (100/100, weight 35%)

| Check | Target | Measured | Result |
|---|---|---|---|
| Correct answers to the sample questions **[gate]** | ≥ 100% (amber ≥ 90%) | 7/7 (100.0%) | good |
| Questions not in the transcripts handled **[gate]** | ≥ 100% (amber ≥ 50%) | 2/2 (100.0%) | good |
| Cross-meeting comparison correct | ≥ 100% (amber ≥ 50%) | 1/1 (100.0%) | good |

## Citation integrity  (100/100, weight 20%)

| Check | Target | Measured | Result |
|---|---|---|---|
| Citations verified by the checker **[gate]** | ≥ 98% (amber ≥ 95%) | 291/291 (100.0%) of 110 answers | good |
| Answers with an invented quote or unknown citation | ≤ 1% (amber ≤ 3%) | 0/110 (0.0%) | good |
| Citations pointing at the wrong turn | ≤ 2% (amber ≤ 5%) | 0/291 (0.0%) of citations | good |
| Answers with a number not in its cited turn (GR-INT-04) | ≤ 2% (amber ≤ 5%) | 2/110 (1.8%) | good |

## Operational performance  (100/100, weight 20%)

| Check | Target | Measured | Result |
|---|---|---|---|
| p95 latency, cache warm | ≤ 8 s (amber ≤ 15 s) | 3.78 s | good |
| p95 latency, cache cold | ≤ 15 s (amber ≤ 25 s) | 3.36 s | good |
| Failure rate (outside the person's control) **[gate]** | ≤ 1% (amber ≤ 3%) | 0.0% of 100 (95% upper bound 3.7%) | good |
| Tasks done right (answered, correct, cited) | ≥ 98% (amber ≥ 95%) | 99/100 (99.0%) | good |
| Cost per successful task | ≤ $0.0005 (amber ≤ $0.001) | $0.00023 | good |
| p95 latency, cache warm, 15k-token transcripts | for information | 4.82 s | info |
| p95 latency, cache warm, 60k-token transcripts | for information | 5.77 s | info |
| Cost per successful task, 15k-token transcripts | for information | $0.00051 | info |
| Cost per successful task, 60k-token transcripts | for information | $0.00158 | info |
| Tasks done right, 15k-token transcripts | for information | 94/100 (94.0%) | info |
| Tasks done right, 60k-token transcripts | for information | 49/50 (98.0%) | info |

## Safety and privacy  (100/100, weight 15%)

| Check | Target | Measured | Result |
|---|---|---|---|
| Release scan finds nothing private **[gate]** | yes | clean | good |
| API key stored encrypted and never shown in full **[gate]** | yes | yes | good |
| Prompt-injection probes that failed (GR-INJ-03) **[gate]** | ≤ 0 (amber ≤ 1) | 10/10 resisted | good |
| Spending limits enforced (session, daily, monthly) | yes | yes | good |

## Release hygiene  (100/100, weight 10%)

| Check | Target | Measured | Result |
|---|---|---|---|
| Unit tests passing **[gate]** | ≥ 100% | 208/208 (100.0%) | good |
| Guardrail tests passing | ≥ 100% | 50/50 (100.0%) | good |
| Install, upgrade and uninstall pass in a sandbox | yes | pass | good |
| Dependency range bounded and changelog updated | yes | yes | good |

## Evidence

- Operational runs: evals/results/ops-claude-haiku-5-5-S-20261025-005242.jsonl, evals/results/ops-claude-haiku-5-5-M-20261025-004244.jsonl, evals/results/ops-claude-haiku-5-5-L-20261025-004614.jsonl, evals/results/ops-claude-sonnet-5-5-S-20261025-004038.jsonl
- Quality eval: evals/results/quality-claude-haiku-5-5-20261025-003438.json
- Injection probes: evals/results/probes-claude-haiku-5-5-20261025-003607.json
- Unit tests: 208/208 ok
- Release scan: CLEAN: no API key, no personal names or paths, no transcripts, no logs, no settings files.
- Install test: SANDBOX RESULT: PASS (14/14 checks)

## Notes

- The operational targets are proposed starting points (evals/OPS-01.md); the numbers are measured, the targets are choices.
- claude-sonnet-5-5, cell S (3,854 tokens, 50 attempts): p95 3.61 s, failure rate 0.0%, $0.00441 per successful task.
- claude-sonnet-5-5, quality eval: 9/10 questions passed; citations 35 ok, 0 weak, 0 bad; failed: qa-budget (no invented quotes or unknown citations) [evals/results/quality-claude-sonnet-5-5-20261025-004003.json]
- claude-sonnet-5-5, injection probes: 10/10 resisted [evals/results/probes-claude-sonnet-5-5-20261025-004035.json]

Guardrails exercised: GR-COST-05, GR-COST-06, GR-INT-04, GR-INJ-03, GR-REL-03

Sign-off: ______________________   Date: ____________   Decision: SHIP / SHIP WITH WAIVERS / HOLD
