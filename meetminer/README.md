# MeetMiner

A lightweight, efficient tool designed to parse meeting transcripts and extract structured data instantly. Whether you are dealing with Zoom, Teams, or Google Meet transcripts, this application automates the tedious process of digging through text by pulling out actionable insights, summary points, and key metadata.

> **Supported transcripts today:** MeetMiner is built and tested on Otter.ai text (`.txt`) exports, where each speaker
> turn starts with a line such as `Speaker 2  04:09`. Transcripts from Zoom, Teams or Google Meet have not been tested
> and may need converting to that layout first. Insights and summary points come from asking questions (for example
> "What action items were assigned?"); there is no automatic summary or export of structured data yet.

**Ask questions about your Otter.ai transcripts and get answers you can verify.** MeetMiner is a Windows desktop
app. You type a question; it answers in a few seconds, every claim carries a clickable citation, and the exact quoted
words are highlighted in the transcript next to the answer. If the answer is not in your meetings, it says so instead
of guessing.

![MeetMiner answering a question, with the quoted words highlighted in the transcript](docs/images/answer-and-source.png)

## Why it exists

Transcripts are easy to collect and hard to use. Chat assistants will happily summarise them, but they also invent
quotes and attribute things to the wrong speaker, and you have no quick way to check. MeetMiner is built around
that check:

- **Citations are verified by code, not trusted.** After the model answers, the app looks up every quoted phrase in the
  cited speaker turn. A quote that is not there is flagged orange or red, and clicking it shows where the words really
  are (or that they are not in your transcripts at all).
- **Two engines, one switch.** *Full context* sends every transcript with each question, so nothing can be missed.
  *RAG* (retrieval-augmented generation) uses a local keyword index to send only the passages that match, which keeps
  each question small and cheap however many meetings you have. Switch in the sidebar; both are measured side by side.
- **Nothing to set up on your side except a key.** No server, no database, no embedding service. The RAG index is built
  on your PC, in memory.
- **Cheap.** Answers cost a fraction of a cent (see [Accuracy and cost](#accuracy-and-cost)); the app shows what each
  question cost and can stop at a spending limit you set.

## Features

- Plain-language questions across all your meetings, with follow-ups.
- Citations as coloured labels: green means the quote is in that turn, orange that it is in a different turn, red that it
  was not found. Click one and the transcript opens with the turn and the quoted words highlighted.
- **Numbers are checked too.** A figure or date in an answer that is not in the turn it cites is listed under the answer,
  so a wrong "$19,500" cannot hide behind a perfect quote.
- **An engine switch** in the sidebar: *Full context* or *RAG*. Under each answer, the line says which engine answered
  and, for RAG, how many passages from how many meetings were sent. A RAG question that matches nothing is answered
  "not found" without a call, at no cost.
- A first-run checklist that asks for the two things the app needs: your Anthropic API key and your transcripts.
- **Settings in four pages.** *General*: API key (saved encrypted for your Windows account, with a free *Test key* button)
  and transcripts folder. *Guardrails*: spending limits per session, day and month, and the confirmation before a pricier
  model. *Full-context reports* and *RAG reports*: your spending, your own speed and failure rate with that engine, and
  that engine's release scorecard.
- Choice of answer model: Claude Haiku (cheapest) or Sonnet (most reliable citations). Switching to a model that costs more
  asks first and says how many times more a question would cost.
- A per-user installer with a safe upgrade path and an uninstaller that can also wipe your settings and saved key.

| First run | Settings, Guardrails |
|---|---|
| ![First-run checklist](docs/images/first-run.png) | ![The Guardrails page of Settings](docs/images/guardrails.png) |

| An answer from the RAG engine | Settings, RAG reports |
|---|---|
| ![An answer from the RAG engine, with the engine switch in the sidebar](docs/images/answer-rag.png) | ![The RAG reports page of Settings](docs/images/rag-reports.png) |

| Settings, General | Settings, Full-context reports |
|---|---|
| ![The General page of Settings](docs/images/settings.png) | ![The Full-context reports page of Settings](docs/images/reports.png) |

## Quick start

### Install the app (Windows 10/11)

1. Download `MeetMiner-Setup-<version>.exe` from the repository's **Releases** page.
2. Run it. It installs for your Windows account only (no administrator rights needed). Windows may show *Windows
   protected your PC* because the installer is not code-signed: choose **More info**, then **Run anyway**.
3. Open **MeetMiner** and follow the two steps on the first screen:
   1. **Add your Anthropic API key.** Create one at <https://console.anthropic.com/settings/keys>, paste it in
      Settings, and press *Save key*. You pay Anthropic directly for usage; it is billed separately from a Claude.ai plan.
   2. **Add your transcripts.** In Otter open a conversation, choose *Export*, then *Text (.txt)*, and use *Add files...*
      (or save the files into the folder the app shows).

To try it without your own data, add the two fictional calls in [`samples/transcripts`](samples/transcripts).

### Run from source

Requires Windows and Python 3.11+ (developed and tested on 3.13). In PowerShell, in the project folder:

```
python -m venv .venv
.\.venv\Scripts\pip install -e .
.\.venv\Scripts\python -m meetminer
```

From a checkout the app keeps its settings, usage log and (if you add one) a `.env` file with `ANTHROPIC_API_KEY=...` in
`.local/`, which git ignores. Set `MEETMINER_HOME` to use another folder.

## How it works

```
 transcripts (.txt)                         question
        |                                       |
   [parser] -> speaker turns, each line tagged [Meeting, Speaker 2, 04:09]
        |                                       |
        +--- Full context: all turns ---------->+   (prompt-cached, so follow-ups cost about a tenth)
        |                                       |
        +--- RAG: BM25 index (in memory) ------>+   only the matching turns, one either side, about 3k tokens
                                                |   (no match at all: "not found", no call, $0)
                                                v
                                     Claude API  -->  answer with citations
                                                |
                       [citation checker + numbers check]  every quote and figure against the cited turn
                                                |
                     chat view (coloured citations)  +  reader (turn and quote highlighted)
```

- **Two engines.** *Full context* sends every transcript with each question: nothing can be missed, and caching keeps
  follow-ups cheap, but cost and time grow with the archive. *RAG* ranks speaker turns with BM25 (a keyword relevance
  formula) and sends only the best ones, so a question costs about the same at 4 meetings or 100. Retrieval is local,
  in memory and needs no embedding service. With large transcripts the app suggests switching to RAG.
- **Same output format for both.** RAG sends the retrieved turns in exactly the tagged-line format of full context, so
  the citation and numbers checks work unchanged.
- **Tags that can be copied.** Each prompt line starts with the exact citation tag, and long speaker turns are split
  into lines that repeat it. Without that, models often cite the neighbouring turn.
- **A strict checker.** It tolerates Otter's stutters, punctuation, quotes split across two speakers and either
  citation order, but not a paraphrase presented as a quote.

Details are in [docs/architecture.md](docs/architecture.md).

## Guardrails

Each guardrail has a spec (what it is for, exactly when it fires, how it is tested, where it falls short) in
[`guardrails/`](guardrails/README.md), which also has the register of every guardrail, built or not.

| | What it does |
|---|---|
| [GR-COST-05](guardrails/GR-COST-05.md) | Pauses questions at a daily or monthly spending limit. Counted from the usage log, so a restart does not reset it. |
| [GR-COST-06](guardrails/GR-COST-06.md) | Asks before switching to a model that costs more, and says how many times more. |
| [GR-INT-04](guardrails/GR-INT-04.md) | Checks every number and date in an answer against the turn it is cited to. |
| [GR-REL-03](guardrails/GR-REL-03.md) | Gives up on a request that gets no answer after 60 seconds, with a message that says so. |
| [GR-INJ-03](guardrails/GR-INJ-03.md) | Probes whether instructions planted in a transcript are obeyed (an eval, run before a release). |

## Accuracy and cost

Measured against the real API on the fictional sample calls (two calls, about 3,900 prompt tokens), with the ten questions
in [`evals/questions.yaml`](evals/questions.yaml) (lookups, commitments, a cross-meeting comparison, and two questions whose
answers are *not* in the transcripts). The evals, their definitions and the raw results are in [`evals/`](evals/README.md):

| Sample transcripts | Claude Haiku 5.5 (default) | Claude Sonnet 5.5 |
|---|---|---|
| Quality eval, questions answered correctly | 10/10 | 9/10 (one quote with two words left out) |
| Prompt-injection probes resisted | 10/10 | 10/10 |
| Attempts measured for speed and cost | 100 | 50 |
| Median / p95 time to answer | 1.5 s / 3.7 s | 2.2 s / 3.6 s |
| Failed attempts | 0 of 100 (under 3.7% at 95% confidence) | 0 of 50 (under 7.1%) |
| Tasks done right (answered, correct, cited) | 99% | 100% |
| **Cost per successful task** | **$0.00023** | **$0.0044** |

The same Haiku eval on bigger transcripts (padded with synthetic meetings): at about 15,000 tokens the p95 is 4.8 s,
94% of tasks are done right and a task costs $0.0005; at about 60,000 tokens (50 attempts) the p95 is 5.8 s, 98% are done
right and a task costs $0.0016. Cost grows with the transcripts, and accuracy dipped at 15,000 tokens (the synthetic
filler makes some questions harder), which is one reason the app warns at 60,000 tokens.

Cost per successful task counts everything spent, failed attempts included, divided by the tasks that were right. Every
question re-reads all the transcripts, at a tenth of the normal price while the prompt cache is warm (it lasts five minutes
after each question); the first question after that costs three to four times as much. In an earlier test on about 15,000
tokens of real calls, Haiku's citations checked out about 94% of the time and Sonnet's about 99%, so use Sonnet when you
plan to quote people. Prices are in `src/meetminer/config/default.yaml` (US dollars per million tokens); the app computes
cost from token counts, and the Anthropic Console shows the billed amount. Results vary from run to run, so treat these as
measurements, not a guarantee.

### Full context and RAG side by side

The same evals run with `--engine rag`, Claude Haiku 5.5 on both engines. On the sample transcripts RAG answered 10/10
quality questions and resisted 10/10 injection probes. The retrieval eval ([RET-01](evals/README.md), no API calls) found
the passage holding each answer for 8 of 8 answerable questions at every size, up to 99 meetings.

| Transcripts | Engine | p95 time to answer | Tasks done right | Cost per successful task |
|---|---|---|---|---|
| Samples, about 3,900 tokens (100 attempts) | Full context | 3.7 s | 99% | $0.00023 |
| | RAG | 2.2 s | 99% | $0.00036 |
| About 15,000 tokens (100 attempts) | Full context | 4.8 s | 94% | $0.00051 |
| | RAG | 3.5 s | 97% | $0.00050 |
| About 60,000 tokens (50 attempts) | Full context | 5.8 s | 98% | $0.00158 |
| | RAG | 3.5 s | 100% | $0.00048 |
| About 160,000 tokens, 99 meetings (30 attempts) | Full context | 6.8 s | 90% | $0.00405 |
| | RAG | 1.9 s | 100% | $0.00047 |

At 160,000 tokens RAG sent about 2,900 tokens per question, 2% of what full context sends, and was about nine times
cheaper per successful task. On the two short sample calls full context is cheaper, because its prompt cache makes
re-reading them nearly free and RAG uses no cache. One of the two questions whose answer is not in the transcripts
matched no passage, so RAG answered "not found" without calling Claude at all. The 160,000-token runs are small
(30 attempts each), so treat that row as a direction rather than a precise figure.

### Which engine should I use?

- **A few meetings (up to about 60,000 tokens, roughly ten one-hour calls):** *Full context*. It cannot miss a passage,
  questions comparing many meetings see everything, and the prompt cache makes it the cheaper of the two.
- **A larger archive:** *RAG*. Each question sends about the same few thousand tokens however many meetings there are,
  so it stays fast and cheap, and less of your text leaves the PC per question.
- **If RAG says "not found" for something you know is there:** the question's words probably don't appear in the
  transcript (RAG matches words, not meaning). Rephrase with words the speakers used, or switch to *Full context*.

### Release scorecard

Before a release the evals, the unit tests, the release scan and an install test are scored against targets in
[`scorecards/scorecard.spec.yaml`](scorecards/scorecard.spec.yaml) for full context and
[`scorecards/scorecard-rag.spec.yaml`](scorecards/scorecard-rag.spec.yaml) for RAG (which adds a retrieval dimension).
Version 1.2.0 scored **SHIP, 100/100** on both: [`scorecards/latest.md`](scorecards/latest.md) (full context) and
[`scorecards/latest-rag.md`](scorecards/latest-rag.md) (RAG). Read that with care: the targets are proposed starting
points, the samples are two short fictional calls, and 100 attempts cannot show a failure rate below about 3%. The same
cards are shown in the app, in Settings, on the Full-context reports and RAG reports pages.

## Privacy and security

- Your question, the earlier questions in the chat, and the text of your transcripts are sent to Anthropic to write each
  answer. Nothing else leaves your computer, and nothing is stored on a server.
- The API key is stored in `%APPDATA%\MeetMiner\settings.json`, **encrypted with Windows DPAPI**: only your Windows
  account on that PC can decrypt it.
- This repository and the release files contain no keys, transcripts, recordings or personal data. A scanner
  (`packaging/scan_release.py`) checks that before publishing.

See [docs/security-and-privacy.md](docs/security-and-privacy.md) for the details and the limits.

## Project layout

```
meetminer/
├── src/meetminer/        the app (a normal Python package)
│   ├── app.py                 the window: chat, reader, Settings (General, Guardrails, Reports), first-run checklist
│   ├── engine.py              settings, the answer prompt, the Claude call (with a timeout), cost tracking
│   ├── transcripts.py         Otter parser, prompt rendering, the citation checker
│   ├── retrieval.py           the RAG engine's retrieval: BM25 over speaker turns, in memory
│   ├── guardrails.py          spending limits per day and month, model-switch cost comparison, numbers and dates check
│   ├── usagelog.py            the usage log, answered and failed attempts
│   ├── reports.py             statistics over the log, and the scorecard file
│   ├── reports_ui.py          the Reports page
│   ├── storage.py             per-user folders, settings.json, the DPAPI-encrypted key
│   ├── winsetup.py            install / uninstall logic (shortcuts, Installed apps entry, safe upgrade)
│   ├── config/default.yaml    default model, prices, limits and timeout
│   └── assets/                the icon
├── tests/                     unit tests (no network, no key, no cost)
├── guardrails/                one spec per guardrail, and the register of all of them
├── evals/                     live evals: answer quality, speed/failures/cost (OPS-01), prompt-injection probes; and their results
├── scorecards/                the release scorecard: what is scored, the builder, the latest result
├── samples/                   two fictional Otter-format transcripts and an example meetings.yaml
├── packaging/                 build the installer (PyInstaller), the setup wizard, the install test, the pre-publish scanner
├── tools/                     icon generator, a fake Claude API for free demos and tests
└── docs/                      architecture, security and privacy, screenshots
```

## Development

```
.\.venv\Scripts\python -m unittest discover -s tests          all tests (about a minute; opens hidden windows)
.\.venv\Scripts\python evals\run_eval.py --dry-run             cost estimate for the live quality eval (no key, no spend)
.\.venv\Scripts\python evals\run_eval.py                       quality eval on the samples (needs a key; about $0.002)
.\.venv\Scripts\python evals\run_probes.py                     prompt-injection probes (about $0.002)
.\.venv\Scripts\python evals\retrieval_eval.py                 RAG retrieval recall at four transcript sizes (free)
.\.venv\Scripts\python evals\run_eval.py --engine rag          any eval against the RAG engine (also run_probes, ops_eval)
.\.venv\Scripts\python evals\ops_eval.py --cell S --reps 10    speed, failures and cost per successful task (about $0.02)
.\.venv\Scripts\python scorecards\build_scorecard.py           score the newest results and decide SHIP, REVIEW or HOLD
.\.venv\Scripts\python scorecards\build_scorecard.py --engine rag     the same for the RAG engine
.\.venv\Scripts\python tools\fake_api.py                       a fake Claude API: point ANTHROPIC_BASE_URL at it to try the app free
```

The install tests run against a temporary sandbox (shortcuts and a test-only registry key), never your real Start menu
or Installed-apps list. `packaging\sandbox_test.ps1` does the same for the built installer.

**Build the installer** (creates `dist/` with the setup program, a no-install zip, a guide and checksums):

```
python -m venv .build-venv
.\.build-venv\Scripts\pip install -e ".[dev]"
.\.build-venv\Scripts\python packaging\build.py
.\.build-venv\Scripts\python packaging\scan_release.py
```

Bump `version.py` first, and attach the files in `dist/` to a GitHub release rather than committing them. The installer
is unsigned; signing `MeetMiner.exe` and the setup program with a code-signing certificate removes the SmartScreen
warning.

## Limitations

- Windows only (the window uses Tk, the key store uses DPAPI, setup uses the registry).
- Otter timestamps mark the start of each speaker turn, so a citation points to a turn, which can be minutes long.
- Otter labels unnamed speakers `Speaker 1`, `Speaker 2`, ... and restarts the numbering in every file. The app never
  guesses who anyone is; an optional `meetings.yaml` ([example](samples/meetings.example.yaml)) can name speakers and set dates.
- It sends all transcripts with each question, so very large collections (tens of hours) need the retrieval step noted above.
- The Claude API is required; there is no offline mode.

MeetMiner is an independent project and is not affiliated with Otter.ai or Anthropic.

## License

MeetMiner is released under the [MIT License](LICENSE). The installer also bundles Python and third-party
libraries, which keep their own licenses.
