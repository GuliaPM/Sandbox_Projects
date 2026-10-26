# Security and privacy

## What leaves your computer

Each question sends this to Anthropic's API (`api.anthropic.com`, over HTTPS):

- your question and up to six earlier questions and answers from the current chat;
- with the **Full context** engine, the text of **all** your transcripts; with the **RAG** engine, only the passages
  that match the question (typically a few thousand tokens), and nothing at all when no passage matches;
- the instructions that tell the model how to answer and cite.

The RAG engine's index is built on this PC, in memory, from the transcripts already loaded. It is never written to disk
and no other service (such as an embedding API) sees your transcripts. Of the two engines, RAG sends the least: a leaked
request would expose one answer's excerpts, not the whole archive.

Nothing else is sent: no files from outside the transcripts folder, no analytics, no telemetry, no crash reports. The
free *Test key* button and the token count call Anthropic's token-counting endpoint, which receives the same text.
Anthropic's own data-handling terms apply to what it receives; read them if your meetings are sensitive.

## The API key

- Entered in **Settings**, stored in `%APPDATA%\MeetMiner\settings.json` as `dpapi:<base64>`, encrypted with the
  Windows Data Protection API. It can be decrypted only by the same Windows user on the same machine; copying the file
  elsewhere yields nothing. The test-suite checks that the plain key never appears in the file.
- Never written to a log, shown in full (Settings shows the first seven and last four characters), or placed in the
  command line. The setup program never contains a key.
- `ANTHROPIC_API_KEY` in the environment is also honoured, and wins over the saved key. From a source checkout a
  `.local/.env` file works too.
- *Remove key* in Settings, or uninstalling with the "delete my settings" box ticked, deletes it.
- Use a key with a **spending limit** set in the Anthropic Console, and revoke it there if you ever think it leaked.

## What is stored on disk

| File | Contents |
|---|---|
| `settings.json` | encrypted key, chosen transcripts folder, spending limit |
| `usage.csv` | time, model, token counts, cost, seconds, whether the attempt worked (and why not), and the **first 80 characters of each question** |
| `desktop-error.log` | only if the app crashed: the Python traceback |
| your transcripts | wherever you keep them; the app only reads them (and copies files you add into its folder) |

Delete `usage.csv` at any time. Nothing needs it, but the daily and monthly spending limits (GR-COST-05) and the Reports page
are counted from it, so deleting it also resets those totals to zero.

## What the releases and this repository contain

The installer, the no-install zip and this repository contain no API keys, transcripts, recordings, logs, settings files or
personal data. The only transcripts in the repository are the two fictional calls in `samples/`. This is checked, not
assumed: `packaging/scan_release.py` searches the release files (and the text and file paths compiled into the app) and any
source tree for key patterns, the machine's own paths, the real key in `.local/.env`, terms listed in a private
`.local/scan-needles.txt`, and files that must never ship (`.env`, `settings.json`, logs, recordings, shortcuts, real
transcripts). A planted test key, name and transcript line are all caught; the scan runs as the last step of every build.

## Limits you should know about

- **The installer is not code-signed.** Windows SmartScreen shows a warning until the file has a reputation or is signed with
  a certificate. Verify downloads against `SHA256SUMS.txt`. Some antivirus programs distrust unsigned PyInstaller programs.
- **Output is checked, not guaranteed.** The citation checker catches invented or misplaced quotes; it cannot tell whether a
  paraphrase is a fair summary. Click a citation before you rely on or repeat a claim.
- **Transcript text is untrusted input.** The prompt tells the model to ignore instructions inside transcripts, and the
  model has no tools, so there is nothing for such text to trigger beyond a wrong answer. This is tested, not assumed:
  `evals/run_probes.py` plants ten kinds of attack in a fictional transcript (override, fake system message, a link that
  would carry the question out, a false price, a language switch, a prompt leak...) and checks the answers
  (GR-INJ-03, see [../guardrails/GR-INJ-03.md](../guardrails/GR-INJ-03.md)). It shows how the defences held on those attacks
  with the model it was run on; it does not block anything in the live app.
- **Numbers are checked, not trusted.** A figure or date in an answer that is not in the cited turn is listed under the
  answer (GR-INT-04). A calculated figure is listed too: the check cannot tell a sum from a mistake.
- **Windows only**, and the per-user key encryption protects against other users and stolen files, not against malware
  already running as you.

## Reporting a problem

Open an issue on the repository, or contact the repository owner. Do not post transcripts or keys in an issue.
