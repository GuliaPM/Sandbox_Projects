# Architecture

MeetMiner is a single-process Windows desktop app. This page explains how the pieces fit together and why they
are built the way they are.

## Modules (`src/meetminer/`)

| Module | Responsibility | Depends on |
|---|---|---|
| `transcripts.py` | Parse Otter `.txt` exports into meetings and turns; render the prompt text; check citations | standard library, PyYAML |
| `retrieval.py` | The RAG engine's retrieval: BM25 index over speaker turns (in memory), choosing and rendering the excerpts | `transcripts` |
| `storage.py` | Where files live; `settings.json`; the DPAPI-encrypted API key; default transcripts folder | standard library |
| `usagelog.py` | The usage log (`usage.csv`): one line per attempt, answered or failed; reads old layouts | standard library |
| `guardrails.py` | Daily and monthly spending limits, the model-switch cost comparison, the numbers and dates check | `transcripts`, `usagelog` |
| `engine.py` | Merge settings; build the answer prompt for either engine; call the Claude API with a timeout; count tokens and cost; log usage | `transcripts`, `retrieval`, `storage`, `usagelog`, `guardrails`, `anthropic` |
| `reports.py` | Statistics over the usage log (p50, p95, failure rate, cost per answer) and the scorecard file | `storage`, `usagelog` |
| `reports_ui.py` | The Reports page of the Settings window | `reports`, `guardrails`, `usagelog` |
| `app.py` | The Tk window: chat, reader, Settings (General, Guardrails, Reports), first-run checklist; runs the engine in a background thread | `engine`, `guardrails`, `reports_ui`, `storage`, `transcripts` |
| `winsetup.py` | Install, upgrade and uninstall logic shared by the setup wizard and `MeetMiner.exe --uninstall` | `storage` |

`packaging/installer.py` (the setup wizard) and `packaging/build.py` sit outside the package: they are only needed to
produce the Windows installer.

## From question to verified answer

1. **Parse.** `load_meetings()` reads every `.txt` in the transcripts folder. A line holding a speaker label and a
   timestamp (`Speaker 2  04:09`, or a real name) starts a turn; Otter's "Transcribed by otter.ai" footer is dropped.
   Titles come from file names, dates and speaker names from an optional `meetings.yaml`.
2. **Render.** `render_corpus()` writes one block for all meetings. Every line starts with the citation tag
   `[Meeting title, Speaker 2, 04:09]`; turns longer than about 90 words are split at sentence ends and each piece repeats
   the tag. The block is deterministic, so it can sit in front of a prompt-cache breakpoint.
3. **Ask.** `MeetingQA.ask()` sends the rules and the transcripts as one cached system block and the question (plus up to
   six earlier exchanges) as messages. It never sends `temperature`, a prefill or a forced tool choice, which current
   models reject. It sets `output_config={"effort": "low"}` (the cheapest setting; the sample eval did not need more).
4. **Check.** `check_citations()` finds every `[Meeting, Speaker, time]` tag, resolves it to a turn, and looks for each
   quoted phrase inside that turn (normalising case, punctuation, curly quotes and Otter's repeated words, allowing a
   quote to cross into the next turn). Results: *ok* (quote in the cited turn), *weak* (the words exist but in another
   turn; the reader jumps there), *bad* (not found anywhere, or the tag points at nothing).
5. **Check the numbers.** `guardrails.check_numbers()` looks up every number and date in the answer in the turn it is
   cited to (GR-INT-04). The text in front of a citation is what that citation supports; an amount that is in another
   turn, or in no transcript at all, is listed under the answer.
6. **Show.** `app.py` renders the model's light markdown, turns each tag into a coloured clickable label, and on a click
   opens that meeting in the reader with the cited turn and the quoted words highlighted.

### Two engines: full context and RAG

The switch in the sidebar picks how the transcripts reach Claude. Everything after the call (citation check, numbers
check, guardrails, usage log) is the same for both.

| | Full context | RAG |
|---|---|---|
| What is sent | every transcript, every question | the passages that match, with one turn either side, up to about 6,000 tokens |
| How it is picked | nothing to pick | BM25 keyword ranking over speaker-turn chunks (`retrieval.py`), light stemming, a short synonym list, and the best passage of every other meeting that matches well |
| Prompt cache | yes: the transcripts block is identical every time | no: the excerpts change with every question, and a cache write costs 1.25x |
| Can it miss the answer? | no, everything is there | yes, if retrieval does not pick the turn (measured by RET-01) |
| Grows with the transcripts | cost and latency | almost nothing: about 3,000 tokens whatever the size |
| Not found | the model says so | the model says so, or, when no passage matches at all, the engine answers without a call (GR-RAG-01) |

The retrieved turns are written in exactly the format the full-context engine uses (`[Meeting, Speaker 2, 04:09] ...`),
grouped by meeting in time order with `[... turns not shown ...]` at the gaps, so the citation tags the model copies
resolve to the same turns and the checker needs no change. The system rules are the same, except that they say the text
is excerpts and that "not found" means "not in these excerpts".

The index is built in memory the first time a RAG question is asked (a few milliseconds even for 100 meetings), rebuilt
when the transcripts are reloaded, and never written to disk. There is no embedding model and no extra service: the only
party that sees transcript text is still Anthropic, and it now sees less of it per question.

### Before and after the call

Around the call itself, the engine does three more things (each is a guardrail with a spec in `../guardrails/`):

- **Before:** `guardrails.spend_limit_error()` sums today's and this month's cost from the usage log and refuses the question
  when a limit is reached (GR-COST-05), in addition to the per-session limit.
- **During:** the Anthropic client is built with `request_timeout_seconds` and `max_retries` (GR-REL-03), so a request that
  gets no answer ends in a clear message, and a timeout is told apart from a dropped connection.
- **After:** every attempt, answered or failed, is written to `usage.csv` with a status and a kind of failure, and the
  Reports page reads the log back.

In the window, switching the answer model asks first when the new model costs more (GR-COST-06).

### When to use which engine

For a few hours of meetings, full context is the simpler choice: it cannot miss a passage, and caching makes follow-ups
about ten times cheaper on the transcript part. As the transcripts grow, every question costs more and takes longer, and
the evals show accuracy dipping as the context fills with unrelated meetings. RAG keeps each question small whatever the
archive size, at the risk of not retrieving the right turn. The app warns when the transcripts pass
`retrieval_threshold_tokens` (60,000) and offers to switch to RAG. The measured comparison is in the README and in the
two scorecards.

### What the citation checker deliberately does not trust

- A tag with the wrong time or speaker, even if the quote exists elsewhere (flagged *weak*, and the reader shows the real turn).
- A paraphrase presented inside quotation marks, or a quote with words changed.
- Quotes that were not cited at all (reported if they are four or more words and not in the transcripts).

It does tolerate short search terms in quotation marks, meeting names, a one-letter quoted token, two tags in one bracket,
and a citation placed before its quote.

## Storage

| What | Installed | From a checkout |
|---|---|---|
| Settings, encrypted key, usage log | `%APPDATA%\MeetMiner` | `.local/` (git-ignored) |
| Transcripts | `Documents\MeetMiner\Transcripts` or the folder chosen in Settings | wherever Settings points |
| Shipped defaults | inside the package: `config/default.yaml` | same |

`MEETMINER_HOME` overrides the first row everywhere; the tests use it so they never touch real data.
Precedence for a setting: environment variable (`ANTHROPIC_MODEL`) over `settings.json` over `default.yaml`.

## The installer

`packaging/build.py` stages the package, builds the app with PyInstaller (a folder build, so it starts quickly),
zips it, and embeds that zip in a second PyInstaller build: the setup wizard. The wizard (`installer.py`) and the
app's own `--uninstall` mode both use `winsetup.py`.

- **Per-user.** Files go to `%LOCALAPPDATA%\Programs\MeetMiner`, shortcuts to the user's Start menu and Desktop, and one
  entry to `HKEY_CURRENT_USER\...\Uninstall`. No administrator rights.
- **Safe upgrade.** The new version is unpacked next to the old one and the two folders are swapped. Windows refuses to rename
  a folder whose program is running, so with the app open the swap fails cleanly (exit code 3) and nothing has changed.
- **Uninstall.** Removes the shortcuts, the registry entry and the install folder (a small script deletes the folder after
  the program exits); optionally deletes settings and the saved key. Transcripts are never touched.
- **Testable without side effects.** `--sandbox DIR` redirects shortcuts and the registry entry; the test-suite uses it.

## Testing, evals and the scorecard

Three layers, each answering a different question:

| Layer | Question | Costs | Where |
|---|---|---|---|
| Unit tests | Does the code do what it was written to do? | nothing, no key, no network | `tests/` |
| Evals | Does the whole app, with the real model, answer well, quickly and safely? | a few cents each | `evals/` |
| Scorecard | Is this version good enough to release? | nothing extra: it scores the above | `scorecards/` |

`tests/` has no network and no key: the engine is exercised with canned answers and fake clients that raise a timeout, a
dropped connection or a bad key; the window with a hidden Tk root; and setup with a sandbox. `evals/` is the only part that
calls the real API: the quality eval, the injection probes (GR-INJ-03) and the operational eval OPS-01 (p95 latency, failure
rate, cost per successful task, for three transcript sizes). `scorecards/build_scorecard.py` reads their results, runs the unit
tests, the release scan and an install test (`packaging/sandbox_test.ps1`), scores everything against
`scorecards/scorecard.spec.yaml` and writes `latest.json`, which the Reports page shows. `tools/fake_api.py` is a
stand-in Claude endpoint with a canned answer (including one invented quote) for trying the app at no cost.

## The Reports page

Settings, Reports has three parts: spending (this session, today and this month against their limits), your own usage over
the last 30 days from `usage.csv` (answers, median and p95 time, failures that were not your doing, average cost, a row per
model), and the release scorecard. The scorecard is looked for in three places, first match wins: a copy in your data folder
(`reports/scorecard.json`), `scorecards/latest.json` in a source checkout, and the one packaged with the installed release
(`meetminer/reports/release-scorecard.json`). "Open report file" loads any other scorecard JSON.
