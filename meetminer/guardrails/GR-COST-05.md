# GR-COST-05: daily and monthly spending limits

```text
GUARDRAIL ID:      GR-COST-05                 NAME: Daily and monthly spending limits      STATUS: in place (1.1.0)
AREA:              cost                       TYPE: preventive       LAYER: code + config + UI
SEVERITY IF MISSING: high                     OWNER: maintainer

RISK ADDRESSED:    The per-session limit starts again at zero every time the app is restarted, so repeated sessions, or
                   a long day on the most expensive model, can spend far more than the person intended without anything
                   in the app noticing.
TRIGGER:           Before every question: the cost logged in usage.csv for today (local calendar day) is at or above the
                   daily limit, or for this calendar month at or above the monthly limit. A warning notice appears when
                   either reaches 80%.
ACTION:            block. No request is sent. The Ask button and the box are disabled, a red notice explains why, and a
                   question that still reaches the engine gets the same message as its answer.
USER MESSAGE:      "Daily spending limit reached ($5.20 of $5.00 today). Raise the daily limit in Settings, or try again
                   tomorrow."   /   "Monthly spending limit reached ($20.10 of $20.00 this month). Raise the monthly
                   limit in Settings, or wait for next month."   /   "85% of the monthly spending limit is used ($17.00
                   of $20.00)."
CONFIG:            max_daily_cost_usd (default 5.00) and max_monthly_cost_usd (default 20.00) in config/default.yaml.
                   Settings, Guardrails saves a person's own values in settings.json, which win. 0 means no limit.
CODE:              src/meetminer/guardrails.py (spend_status, spend_limit_error), src/meetminer/usagelog.py,
                   MeetingQA.ask in engine.py, App.refresh_notices and App.ask in app.py, the Guardrails page.
TEST (must exist): tests/test_guardrails.py, SpendLimitTests: today and month are summed from the log and last month is
                   not counted; the daily limit blocks; the monthly limit blocks even when today is fine; 0 means no
                   limit; the limit blocks the call (the fake client is never called) in two fresh sessions, proving it
                   survives a restart; settings win and keep 0; shipped defaults.
                   tests/test_app.py: a reached limit pauses questions and says so; nearing a limit warns but still
                   answers; raising the limit lifts the pause; the Guardrails page validates and saves the values.
MONITOR:           Reports page, Spending: today, this month and this session against their limits. The scorecard
                   metric "Spending limits enforced" re-runs the tests above before a release.
KNOWN BYPASS / LIMITS:
  - It reads usage.csv. Deleting or moving that file resets the totals.
  - Spending by other programs or other PCs that use the same API key is not counted: the log is per Windows user.
  - The limit is checked before a question, so the question that crosses it still completes (the overshoot is one
    answer, a fraction of a cent on Haiku).
  - Costs come from the price table in config/default.yaml, which must be kept in step with Anthropic's prices.
  - A limit is a convenience, not a billing control. Set a spending limit in the Anthropic Console too.
REVIEW DATE:       at every release
```
