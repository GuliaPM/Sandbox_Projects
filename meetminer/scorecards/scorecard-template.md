# Scorecard template

Copy this file, fill it in, and keep it with the release. For the automatic version run `python scorecards/build_scorecard.py`.

```text
SCORECARD: MeetMiner            RELEASE: <version>      DATE: <date>      REVIEWER: <name>
MODEL / CONFIG: <model, effort>      EVIDENCE: <OPS-01 run ids, quality run, probes run>

SCORING: Green = 2, Amber = 1, Red = 0. A metric nobody measured scores 0 and is written "not measured".
         Dimension score = points / best possible x 100.   TOTAL = the dimension scores weighted by the weights below.
GATE:    a Red on any metric marked [G], or a [G] metric with no evidence, blocks the release whatever the total.
DECISION: SHIP = total >= 85, no red or open gate, nothing unmeasured.
          REVIEW = total >= 70 with a gap.   HOLD = anything else.

DIMENSION / METRIC                         TARGET (green | amber)       MEASURED    RESULT   GATE
1. ANSWER QUALITY (weight 35%)
   Correct answers, sample questions       100% | 90%                   <n/n>                [G]
   Questions with no answer handled        100% | 50%                   <n/n>                [G]
   Cross-meeting comparison correct        100% | 50%                   <n/n>
2. CITATION INTEGRITY (weight 20%)
   Citations verified by the checker       >= 98% | 95%                 <%>                  [G]
   Answers with a bad citation             <= 1% | 3%                   <%>
   Citations at the wrong turn             <= 2% | 5%                   <%>
   Answers with a number not in its turn   <= 2% | 5%                   <%>
3. OPERATIONAL PERFORMANCE (weight 20%)  (from OPS-01)
   p95 latency, cache warm                 <= 8 s | 15 s                <s>
   p95 latency, cache cold                 <= 15 s | 25 s               <s>
   Failure rate                            <= 1% | 3%                   <%>                  [G]
   Tasks done right                        >= 98% | 95%                 <%>
   Cost per successful task                <= <green for the model> | 2x  <$>
4. SAFETY AND PRIVACY (weight 15%)
   Release scan                            clean                        <result>             [G]
   Key encrypted, never shown in full      pass                         <test>               [G]
   Prompt-injection probes failed          0 | 1                        <n>                  [G]
   Spending limits enforced                pass                         <tests>
5. RELEASE HYGIENE (weight 10%)
   Unit tests                              all pass                     <n/n>                [G]
   Guardrail tests                         all pass                     <n/n>
   Install / refused upgrade / uninstall   pass                         <result>
   Dependency range and changelog          yes                          <yes/no>

DEFECTS / WAIVERS:  <id | description | owner | due | waiver approved by>
TREND:              <this release against the previous one, by dimension>
DECISION:           SHIP | SHIP WITH WAIVERS | HOLD            SIGN-OFF: <name, date>
```
