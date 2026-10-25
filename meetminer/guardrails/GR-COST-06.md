# GR-COST-06: confirm before switching to a more expensive model

```text
GUARDRAIL ID:      GR-COST-06                 NAME: Model-switch confirmation              STATUS: in place (1.1.0)
AREA:              cost                       TYPE: preventive       LAYER: UI + config
SEVERITY IF MISSING: medium                   OWNER: maintainer

RISK ADDRESSED:    One click on the Answer model box can multiply the cost of every later question (Sonnet costs about
                   20 times more than Haiku per question) without the person realising it.
TRIGGER:           The Answer model box is changed to a model whose estimated cost for a typical warm question is more
                   than 5% higher than the model in use. The estimate uses the price table, the measured size of the
                   person's transcripts (4,000 tokens if it is not known yet), 150 new input tokens and 400 output
                   tokens. Switching to a cheaper model never asks.
ACTION:            ask. A Yes/No box with No as the default; on No the box returns to the model in use and nothing changes.
USER MESSAGE:      "Sonnet 5.5 costs about 20x more per question than Haiku 5.5.

                    For your transcripts (about 3,854 tokens) a typical question would cost about $0.0051 instead of
                    $0.0003. The first question after five idle minutes costs about $0.0139 instead of $0.0007.

                    Switch to Sonnet 5.5?"
CONFIG:            confirm_model_switch (default true) in config/default.yaml; Settings, Guardrails has a checkbox that
                   saves the person's own choice in settings.json.
CODE:              src/meetminer/guardrails.py (compare_models), App.confirm_model_switch and App.on_model in app.py.
TEST (must exist): tests/test_guardrails.py, ModelSwitchTests: Sonnet is about 20x Haiku; the example amounts grow with the
                   size of the transcripts; going cheaper needs no confirmation; a model without prices gives nothing to
                   confirm; the setting is saved.
                   tests/test_app.py: the box asks first with the multiple, the size of the transcripts and No as the
                   default; saying no keeps the model and puts the box back; a cheaper model asks nothing; the
                   confirmation can be switched off.
MONITOR:           The Reports page shows cost per answer by model for the last 30 days, so a switch that was approved
                   shows up as a cost per answer that jumps.
KNOWN BYPASS / LIMITS:
  - It covers the model box only. A model set with the ANTHROPIC_MODEL environment variable, or edited into
    settings.json or config/default.yaml, is not confirmed.
  - The amounts are for a typical question. A long answer, a long chat history or a cold cache costs more.
  - It does not warn about a change of effort level (not available in the window).
REVIEW DATE:       when a model or its price changes
```
