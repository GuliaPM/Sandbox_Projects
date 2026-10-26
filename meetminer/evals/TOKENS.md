# TOK-01: token counts, estimated and real

Measured 2026-10-26 with Anthropic's `count_tokens` endpoint (free, needs a key) by `evals/token_count.py`. Claude Haiku
5.5 and Claude Sonnet 5.5 gave identical counts. Raw results: `results/tokens-20261026-104614.json` (before the fix) and
`results/tokens-20261026-104850.json` (after).

## The cause of the inconsistent numbers

The same XL transcripts were described as "about 150,000", "about 160,000" and "114,328" tokens:

- **150,000** is the *target* in `corpus.py` (`TARGET_TOKENS`). OPS-01 measures each size with `count_tokens` and accepts
  it within 10% of the target; XL measured 162,721, so the target was met and nothing was rebuilt.
- **160,000** in the README and scorecards is that measured 162,721, rounded.
- **114,328** came from RET-01, which, like the retriever, estimated tokens as `len(text) // 4`. These transcripts run at
  2.8 to 2.9 characters per token, not 4, so that rule undercounted by about 1.4x. The RAG scorecard's "2,912 of 114,328
  tokens" was an estimate divided by an estimate.

The same rule set the RAG budget: `rag_max_tokens: 6000` was 6,000 *estimated* tokens, so RAG really sent up to about
9,500 tokens of excerpts per question.

## Whole prompts (full context: rules + transcripts)

| Size | Meetings | Real tokens | Old estimate (chars / 4) | Real / old | New estimate (chars / 2.7) | New / real |
|---|---|---|---|---|---|---|
| S | 2 | 3,854 | 2,817 | 1.37 | 4,175 | 1.08 |
| M | 9 | 15,093 | 10,888 | 1.39 | 16,132 | 1.07 |
| L | 39 | 64,684 | 45,728 | 1.41 | 67,746 | 1.05 |
| XL | 99 | 162,721 | 114,335 | 1.42 | 169,386 | 1.04 |

## RAG excerpts at XL, original questions

"Retriever's estimate" is what the retriever believed it was sending. Before: the old budget arithmetic (`len // 4 + 12`
per line). After: `estimate_tokens` of the rendered excerpts. The rules part of the RAG prompt is 832 real tokens.

| Question | Before: real excerpt tokens | Before: retriever's estimate | After: real excerpt tokens | After: retriever's estimate |
|---|---|---|---|---|
| launch-date | 2,607 | 1,874 | 2,607 | 2,826 |
| qa-budget | 4,756 | 3,210 | 4,756 | 4,961 |
| beta-group | 2,628 | 1,890 | 2,628 | 2,859 |
| api-spec-owner | 2,538 | 1,831 | 2,538 | 2,771 |
| planning-actions | **8,889** | 5,898 | 5,256 | 5,609 |
| seat-pricing | 1,125 | 774 | 1,125 | 1,179 |
| security-documents | **9,552** | 5,945 | 5,457 | 5,581 |
| cross-meeting | 5,916 | 4,003 | 5,382 | 5,650 |
| trap-competitor | 1,501 | 1,063 | 1,501 | 1,615 |
| trap-kubernetes | nothing sent (no match) | | nothing sent | |

Before the fix the real/estimated ratio for excerpts was 1.39 to 1.61, and two questions went over the 6,000 budget by 48%
and 59%. After it, every question stays under 6,000 real tokens of excerpts. The average real RAG prompt at XL went from
5,222 to 4,304 tokens, which is 3.2% and then 2.6% of the full-context prompt (162,721). The old figure of "2%" came from
the two estimates.

## The fix

- `transcripts.estimate_tokens(text)` is now the one estimate in the app: `ceil(len(text) / 2.7)`. Measured ratios were
  2.81 to 2.92 characters per token for whole prompts and 2.74 to 2.95 for excerpts; 2.7 sits just below the lowest, so
  the estimate errs slightly high (by 4% to 8% on whole prompts). A unit test checks it against the newest
  `results/tokens-*.json`: never below the real count, and less than 15% above it.
- The retriever's budget adds up rounded-up estimates of each rendered line, the gap marker and the meeting header, so
  `rag_max_tokens` is a ceiling on real excerpt tokens (the first passage still always goes in).
- RET-01 reports estimates of the prompts exactly as the engines build them. The no-key fallback of
  `count_corpus_tokens`, which feeds the 60,000-token notice when there is no key, uses the same estimate. With a key the
  app counts exactly.

## What sending less changed

RET-01 after the fix, compared with the same day's run before it (both with the audited synonyms): original questions with
easy filler 8/8 at every size (unchanged); with hard filler, `cross-meeting` now also misses its onboarding turn at M
(8/8 to 7/8 at M; L and XL were already 7/8); the held-out results are unchanged. The live RAG results after the fix are in
the README and the RAG scorecard.

## Size labels

Each transcript size is labelled from the measured count, rounded to two significant figures, everywhere in the
repository: **S**, about 3,900 tokens, 2 meetings; **M**, about 15,000 tokens, 9 meetings; **L**, about 65,000 tokens,
39 meetings; **XL**, about 160,000 tokens, 99 meetings. The targets in `corpus.py` are unchanged, so the generated files
are the same. The hard-distractor sizes have the same number of meetings; their estimated sizes are within 1% of the easy
ones (not measured with `count_tokens`).

Hours of talk: the two sample calls are 14.3 minutes of conversation and 3,854 tokens, about 830 of which are the rules
(the RAG prompt with no excerpts measures 832), so they run at about 12,700 tokens per hour. That makes M about one
hour-long call, L about five and XL about a dozen. Earlier documents said "some twenty one-hour calls" for XL and "roughly
ten one-hour calls" for 60,000 tokens; both overstated it. This rate is measured on the two fictional calls only.
