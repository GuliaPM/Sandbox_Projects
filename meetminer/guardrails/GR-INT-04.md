# GR-INT-04: numbers and dates checked against the cited turn

```text
GUARDRAIL ID:      GR-INT-04                  NAME: Numbers and dates check                STATUS: in place (1.1.0)
AREA:              integrity                  TYPE: detective        LAYER: code + UI
SEVERITY IF MISSING: high                     OWNER: maintainer

RISK ADDRESSED:    The quote check only covers words inside quotation marks. A figure, an amount or a date written in
                   the answer's own words ("the budget is $19,500", "the launch is November 15th") is what people act
                   on, and it can be wrong while every quote is perfect.
TRIGGER:           After each answer. The text in front of a citation (back to the previous citation) is what that
                   citation supports. Every number and date in it is looked up in the cited turn:
                     ok         the number is in the cited turn
                     elsewhere  it is in the transcripts, but not in that turn (the finding says where)
                     missing    it is nowhere in the transcripts (it may be a calculation, or invented)
                   A date also has its month compared with the months the cited turn mentions, when it mentions any.
                   Text with no usable citation is only looked up in the transcripts as a whole.
ACTION:            warn. The answer is shown as it is, and a list "Check these numbers against the transcript" follows it.
USER MESSAGE:      "Check these numbers against the transcript:
                      - $19,500: not found in the transcripts (it may be a calculation)
                      - $30: not in the cited turn (it appears in Product planning call, Speaker 2, 01:35)
                      - December 14: the cited turn mentions October, November, not December"
WHAT COUNTS AS A NUMBER: digits with commas, decimals, a $ sign, a % sign, k/thousand/million/billion, ordinals (14th),
                   and numbers written out in words that are 10 or more ("eighteen thousand"). In the transcript every
                   number written in words is understood, so "eighteen thousand dollars" matches "$18,000" and
                   "fourteenth" matches "14th". Times (04:09), numbers in citation tags, "Speaker 3", meeting titles and
                   list numbers are not claims and are skipped. Small counts written as words ("three days") are not
                   checked.
CONFIG:            none (always on). It fails open: if the check itself ever raises an error the answer is shown without it.
CODE:              src/meetminer/guardrails.py (check_numbers, extract_numbers, describe_numbers), MeetingQA.ask in
                   engine.py, App._render_answer in app.py, and the CLI (`python -m meetminer.engine ask`).
TEST (must exist): tests/test_guardrails.py, NumberCheckTests and WordNumberTests: a number in the cited turn;
                   in another turn; nowhere; written as $18,000 / 18k / eighteen thousand; ordinals; a wrong day; a wrong
                   month; percentages; times, speakers, titles and list markers are skipped; numbers before a citation
                   belong to it; consecutive citations share numbers; uncited text; "Not found" answers; a bad citation;
                   and the engine flags a wrong number in a real call. tests/test_app.py: the list is shown under the answer.
MONITOR:           evals/ops_eval.py records, per attempt, how many numbers were checked and flagged; the scorecard
                   metric "Answers with a number that is not in the turn it is cited to" scores it (green at 2% or less).
KNOWN BYPASS / LIMITS:
  - A calculated figure (a total, a discount) is flagged "missing" even when it is right. That is deliberate: it is
    exactly what the reader should check. If the transcript states the result, it passes.
  - The month check only runs when the cited turn names a month, and "May" is never treated as a month (it is also a verb).
  - Small word-numbers in the answer are not checked, and a number attached to the wrong thing is not caught, only a
    number that is not in the cited turn at all.
  - Numbers in uncited text are only looked up in the whole corpus.
REVIEW DATE:       when the numbers metric on the scorecard leaves green
```
