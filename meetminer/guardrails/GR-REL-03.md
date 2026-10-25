# GR-REL-03: request timeout

```text
GUARDRAIL ID:      GR-REL-03                  NAME: Request timeout                        STATUS: in place (1.1.0)
AREA:              reliability                TYPE: preventive       LAYER: code + config
SEVERITY IF MISSING: medium                   OWNER: maintainer

RISK ADDRESSED:    A request that never gets an answer (a dead connection, an overloaded service) leaves the window on
                   "Reading the transcripts..." for as long as the Anthropic library's own default allows, which is ten
                   minutes, with nothing the person can do but close the app.
TRIGGER:           One attempt to reach the Anthropic API gets no answer within request_timeout_seconds (60). The library
                   then tries again up to max_retries (2) times, as it does for a dropped connection, a 429 or a 5xx,
                   so the longest a person can wait is (max_retries + 1) x the timeout, about three minutes.
ACTION:            give up and say so. The window shows the message, takes the next question, and the failed attempt is
                   written to the usage log with status "failed" and kind "timeout" (no cost).
USER MESSAGE:      "The Anthropic API did not answer within 60 seconds. Try again; if it keeps happening, check your
                   connection or try again later."
                   A dropped connection is reported differently ("Could not reach the Anthropic API. Check your internet
                   connection."), so the two can be told apart in the log.
CONFIG:            request_timeout_seconds (60) and max_retries (2) in config/default.yaml. The Guardrails page shows
                   the values in use. They are not editable in the window: a person who needs more patience on a very
                   large set of transcripts can raise the timeout in that file.
CODE:              MeetingQA.client and MeetingQA.ask in src/meetminer/engine.py (APITimeoutError is a kind of
                   APIConnectionError, so it is caught first), usagelog.py, the Guardrails page in app.py.
TEST (must exist): tests/test_guardrails.py, FailureTests: a timeout is reported as one and logged as a failure with no
                   cost; a dropped connection is not called a timeout; the other failures each get their own kind (bad
                   key, rate limit, server error, no credit); the real client is built with the configured timeout and
                   retries; the shipped defaults are a minute and two retries.
MONITOR:           Reports page: "failed for reasons outside your control" over the last 30 days. evals/ops_eval.py counts
                   timeouts in the failure rate (scorecard gate: 1% or less is green, over 3% is red).
KNOWN BYPASS / LIMITS:
  - The timeout applies to each attempt, not to the whole wait. Lower max_retries for a faster failure.
  - It is a limit on silence, not on total time: a response that keeps trickling in is not cut off.
  - The free token count uses the same client and so the same limits. The Test key button uses its own 20 seconds
    and no retries.
  - 60 seconds is generous for answers of up to 2,000 tokens (a few seconds is normal); it would need raising for
    answers that think for a long time.
REVIEW DATE:       when the measured p95 latency on the scorecard changes by a factor of two
```
