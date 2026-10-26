# Changelog

All notable changes to MeetMiner are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/).

## [1.2.1] - unreleased

An audit of what the repository claims. Every result below is measured, and several got worse; they are kept as measured.

### Changed
- **Eval contamination removed from RAG retrieval.** The synonym list mirrored the wording of the eval questions; the
  eval-derived pairs (`price→per`, `document(s)→report/agreement/contract`, `owns→deliver`, `security→soc/review`,
  `discount→prepay`, `invitations→invites`, `signing→sign/signature`) were removed and nothing was added. Audit and
  before/after numbers: `evals/CONTAMINATION.md`.
- **`rag_max_tokens` now means real tokens.** Tokens were estimated as characters / 4, about 1.4 times too low for these
  transcripts, so RAG sent up to 9,552 tokens of excerpts against a 6,000 budget. One shared estimate,
  `transcripts.estimate_tokens` (2.7 characters per token, calibrated with the token-counting endpoint), is now used by
  the retriever, RET-01 and the no-key size check. Details: `evals/TOKENS.md`.
- **One measured size label per transcript size** everywhere: S about 3,900 tokens and 2 meetings, M about 15,000 and 9,
  L about 65,000 and 39, XL about 160,000 and 99. The 1.2.0 entry below gave the XL target (150,000) and overstated it
  as "some twenty one-hour calls"; at the sample calls' rate of about 12,700 tokens per hour it is about a dozen.
- **README opening and limitations.** The first paragraph claimed Zoom, Teams and Google Meet support and "structured
  data extraction"; it now says what the app does (questions about Otter.ai `.txt` transcripts, with quotes and figures
  checked). The limitations now describe both engines, including that RAG matches words, not meaning. The same
  boilerplate was removed from the `pyproject.toml` description, and two stale engine messages were corrected.
- **CI status.** GitHub Actions has run the tests on every push since 1.1.0; GR-REL-09 is marked in place and the
  workflow's "has not been run on GitHub yet" header is gone.

### Added
- **Held-out question set** (`evals/heldout/questions.yaml`): 12 paraphrased questions, model-written and frozen by
  SHA-256 (a unit test fails if the file changes without the hash). Never tune retrieval against it.
- **Near-miss distractors**: `--distractors hard` builds M, L and XL from filler meetings with other launch dates, budgets,
  SOC reports, seat prices and action items. The original filler is unchanged.
- `--questions` on `run_eval.py`, `ops_eval.py` and `retrieval_eval.py`, with follow-up questions (`follows:`);
  `retrieval_eval.py --synonyms pre-audit` reproduces the "before" numbers.
- **TOK-01** (`evals/token_count.py`): real token counts against the estimate (free, needs a key).
- Scorecards: held-out quality and held-out RET-01 as information rows, and a "What this scorecard does and does not
  show" section.

### Results (unedited)
- RET-01, original questions: 8/8 at every size with easy filler; with near-miss filler 7/8 at M, L and XL (the
  cross-meeting question loses its onboarding turn). Held-out questions: 7/10 at S, 5 to 6/10 at M, L and XL.
- Quality, original questions: 10/10 for both engines. Held-out: 10/12 full context, 9/12 RAG.
- RAG scorecard: SHIP 98/100 (it was 100/100): 97% of tasks done right on the sample calls, amber against a 98% target.
  Full-context scorecard: SHIP 100/100.

## [1.2.0] - 2026-10-25

A second engine: RAG, next to full context, with a switch in the window and its own reports.

### Added
- **RAG engine** (`retrieval.py`). A local keyword index (BM25 over speaker turns, light stemming, a short synonym
  list) picks the passages that match the question, with one turn either side, up to about 6,000 tokens. Only those are
  sent to Claude, in the same tagged-line format, so the citation and numbers checks work unchanged. The index is built
  in memory and never written to disk. No embedding service and no new vendor.
- **Engine switch** in the sidebar (Full context | RAG), remembered between sessions, and `--engine` on the command line
  and on every eval. With large transcripts, the size notice offers a one-click switch to RAG.
- **GR-RAG-01**: a RAG question that matches no passage is answered "not found" without calling the API ($0).
- **Two reports pages**: Settings now has *Full-context reports* and *RAG reports*. Each shows the questions that
  engine answered and that engine's scorecard; spending (shared by both) is on both.
- **RET-01 retrieval eval** (`evals/retrieval_eval.py`, free): whether the turns that hold each answer are retrieved,
  for four transcript sizes, and how much less text RAG sends.
- **XL transcript size** (about 150,000 tokens, some twenty one-hour calls) for the speed and cost evals.
- **RAG scorecard** (`scorecards/scorecard-rag.spec.yaml`, `latest-rag.md`), with a retrieval dimension.

### Changed
- The usage log has an `engine` column; older logs are read as full context and upgraded in place.
- `answer_turns` in `evals/questions.yaml` record where each answer is, for measuring retrieval.

## [1.1.0] - 2026-10-25

Guardrails, a release scorecard and a Reports page.

### Added
- **Spending limits per day and per month** (GR-COST-05), read from the usage log so they still apply after a restart.
  Both are set in Settings, Guardrails; a notice appears at 80% and questions pause at 100%.
- **A confirmation before switching to a more expensive model** (GR-COST-06), showing how many times more a question
  would cost for your transcripts. It can be switched off in Settings.
- **Numbers and dates are checked against the turn they are cited to** (GR-INT-04). A figure that is not in the cited
  turn, or not in the transcripts at all, is listed under the answer.
- **A request timeout** (GR-REL-03): a request that gets no answer is given up after 60 seconds with a clear message
  (`request_timeout_seconds` and `max_retries` in config/default.yaml).
- **Prompt-injection probes** (GR-INJ-03): a fictional call with hostile instructions planted in it and a runner that
  checks the app does not obey them (`evals/run_probes.py`).
- **Operational eval OPS-01** (`evals/ops_eval.py`): p95 latency, failure rate and cost per successful task, for three
  transcript sizes, with cold and warm cache measured separately.
- **A release scorecard** (`scorecards/`): scores the eval results, the unit tests, the release scan and an install test
  against targets in `scorecard.spec.yaml` and decides SHIP, REVIEW or HOLD.
- **A Reports page** in Settings: your spending, your own speed and failure rate from the usage log, and the scorecard.
- Settings now has three pages: General, Guardrails and Reports.
- `guardrails/` holds a spec for each guardrail and the register of all of them.
- `packaging/sandbox_test.ps1`: installs, runs, refuses an upgrade, upgrades and uninstalls the built app in a sandbox.

### Changed
- The usage log (usage.csv) now also records attempts that failed, with a status and the kind of failure. An older
  log is upgraded in place.
- `evals/run_eval.py` saves its results in `evals/results/` instead of `last_results.json`.
- A settings.json saved with a byte-order mark (as Windows PowerShell writes it) is read correctly.

## [1.0.0] - 2026-10-24

First public version.

### Added
- Windows desktop app: chat with follow-ups, a reader that highlights the cited turn and the quoted words, a cost meter,
  and a choice of Claude Haiku or Sonnet.
- Citation checker: every quote is looked up in the cited speaker turn; orange and red labels explain what does not match.
- First-run checklist and a Settings window (API key saved encrypted with Windows DPAPI, transcripts folder, spending limit).
- Per-user installer and uninstaller with a safe upgrade (nothing changes if the app is open), built with PyInstaller.
- Fictional sample transcripts, a ten-question live eval, and a test-suite that needs no network or key.
- `packaging/scan_release.py`, which fails a build if a key, personal path or transcript is found, and
  `packaging/export_public.py`, which copies only the publishable files and scans the copy.
- MIT license.
