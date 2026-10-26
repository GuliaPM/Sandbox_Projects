# Changelog

All notable changes to MeetMiner are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/).

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
