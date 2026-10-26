# Eval contamination: audit and held-out results

Written 2026-10-26 for MeetMiner 1.2.1 (unreleased). All numbers below are copied from the result files named in each
table and can be reproduced with the commands at the end.

## The problem

The RAG retriever (`src/meetminer/retrieval.py`) was developed while looking at the ten questions in
`evals/questions.yaml`. Its synonym list mirrored their wording: the question says "documents" and "signing", the answer
turn says "SOC 2 Type II report", "data processing agreement" and "sign", and the list mapped exactly those words onto each
other. RET-01 and the RAG quality eval then measured on the same questions the retriever was tuned on, so their 8/8 and
10/10 showed that the tuning worked, not that retrieval generalises. In addition, the synthetic filler meetings that make
the larger transcript sizes deliberately avoid every fact the questions ask about, so at M, L and XL the retriever only
had to beat easy distractors.

Three changes address this:

1. **Synonym audit.** Every entry was classified; the eval-derived ones were removed (table below).
2. **Held-out set.** `evals/heldout/questions.yaml`: 12 paraphrased questions about the same two calls, frozen by hash
   (`FROZEN.sha256`, checked by `tests/test_contamination.py`). Nothing may be tuned against it.
3. **Hard distractors.** `--distractors hard` builds the M, L and XL sizes from filler meetings with near-miss facts:
   another project's launch moving, another contractor budget, another vendor's SOC 2 Type I and SOC 1 reports, other
   seat prices and prepay discounts, other pilots and invite dates, other people's specs, mockups and action items. Each
   differs from the real answer (other months, amounts, names; titles never contain "planning" or "onboarding"), so the
   expected answers stay unique to their sample meeting. The original ("easy") filler is unchanged byte for byte, which a
   test checks, so earlier results stay comparable.

## 1. Synonym audit

GENERIC: an ordinary English synonym or word form that would help with any meeting. EVAL-DERIVED: exists mainly because an
eval question uses the key word and the alternative is the wording of that question's answer turn, or is not a synonym at
all. Audited per pair (key -> alternative), because several keys mixed both kinds.

| Key | Alternative | Class | Reasoning |
|---|---|---|---|
| price | cost, pricing, fee | GENERIC | Ordinary synonyms and word forms. |
| price | per | EVAL-DERIVED | "per" is not a synonym of price; it is the wording of the seat-pricing answer ("$25 per seat"). |
| cost | price, pricing, spend | GENERIC | Ordinary synonyms. |
| fee | price, cost | GENERIC | Ordinary synonyms. |
| document | paperwork | GENERIC | Ordinary synonym. |
| document, documents | report, agreement, contract | EVAL-DERIVED | Specific kinds of document chosen to match the security-documents answer turn ("SOC 2 Type II report", "data processing agreement"). "contract" matches nothing in the samples but was part of the same expansion. `documents` had no other alternative, so the whole entry went. |
| deadline / due | due, deadline, date | GENERIC | Ordinary synonyms. |
| when | date, day | GENERIC | A "when" question asks for a date in any meeting. |
| budget / money | money, spend, dollars, budget | GENERIC | Ordinary synonyms. |
| action | task, follow | GENERIC | "action item", "task", "follow-up" are the usual words for any meeting. |
| owner | own, responsible | GENERIC | Word form and synonym. |
| owns | own, owner | GENERIC | Word forms. |
| owns | deliver | EVAL-DERIVED | Not a synonym; it is the verb of the API-spec answer turn ("I can deliver the first complete version of the API spec"). |
| decided / decision | decision, decided, agreed, agree | GENERIC | Word forms and synonyms. |
| security | compliance | GENERIC | Ordinary near-synonym. |
| security | soc | EVAL-DERIVED | "SOC" is the specific report in the answer turn, not a synonym of security. |
| security | review | EVAL-DERIVED | A collocation ("security review"), not a synonym; it matches "finance and security both have to review" in the answer turn. |
| launch | release, ship, live | GENERIC | Ordinary synonyms. |
| customers | users, clients | GENERIC | Ordinary synonyms. |
| invitations | invites, invite | EVAL-DERIVED | Maps the question's word onto the answer's word. It had no effect: the stemmer already reduces all three to "invit". |
| signing | sign, signature | EVAL-DERIVED | Maps the question's word onto the answer turn's words ("before we sign", "any signature"). "sign" was redundant with the stemmer. |
| discount | prepay | EVAL-DERIVED | Not a synonym; it is the wording of the seat-pricing answer ("If you prepay for the full year"). |
| discount | off | GENERIC | "10% off" is an ordinary way to say discount. It has no effect: "off" is a stopword. |
| meeting / call | call, meeting | GENERIC | Ordinary synonyms; Otter titles say "call". |

Not audited in this pass: the stopword list and the stemmer's suffix list. Some GENERIC entries also help the original
questions (for example `when -> date`); they stay because they would help with any meeting. The list as shipped in 1.2.0
is kept in `evals/retrieval_eval.py` (`PRE_AUDIT_SYNONYMS`) only so the "before" numbers can be reproduced.

## 2. RET-01, before and after the audit (free, no API)

"Answers found" counts questions whose every answer turn was retrieved. Before = `--synonyms pre-audit` (the 1.2.0 list);
after = the audited list. Held-out results are identical before and after, because the held-out questions never use the
removed key words.

| Questions | Distractors | Synonyms | S | M | L | XL | Not-found traps ended without a call |
|---|---|---|---|---|---|---|---|
| original (8 answerable) | easy | before | 8/8 | 8/8 | 8/8 | 8/8 | 1/2 |
| original | easy | after | 8/8 | 8/8 | 8/8 | 8/8 | 1/2 |
| original | hard | before | 8/8 | 8/8 | 8/8 | 8/8 | 1/2 |
| original | hard | after | 8/8 | 8/8 | **7/8** | **7/8** | 1/2 |
| held-out (10 answerable) | easy | before | 7/10 | 6/10 | 5/10 | 5/10 | 0/2 |
| held-out | easy | after | 7/10 | 6/10 | 5/10 | 5/10 | 0/2 |
| held-out | hard | before | 7/10 | 5/10 | 6/10 | 6/10 | 0/2 |
| held-out | hard | after | 7/10 | 5/10 | 6/10 | 6/10 | 0/2 |

Sizes: S is the two sample calls; M, L and XL add 7, 37 and 97 filler meetings (9, 39 and 99 meetings in total).

What got worse, and why:

- **Original questions, hard distractors, L and XL: 8/8 to 7/8.** `cross-meeting` ("Which meeting discussed a security
  document and which discussed a QA budget?") no longer retrieves the onboarding turn at 02:15. Without `security -> soc`,
  the near-miss sentences about other vendors' SOC reports and security checks outrank it.
- **Held-out questions miss at every size, with or without the audit.** At S: `ho-outside-testers` (the $18,000
  contractor budget asked as "spending ceiling ... outsiders to check quality"), `ho-night-theme` (dark mode asked as
  "low-light colour scheme") and `ho-compliance-paperwork` (SOC 2 and the data processing agreement asked as "compliance
  paperwork"). Larger sizes add misses on `ho-cross` (the onboarding half), `ho-login-person` (easy L/XL) and
  `ho-go-out-gate` (hard M). BM25 matches words, not meaning: a paraphrase that shares no word with the answer turn is
  not found.
- **Held-out traps: 0/2 end without a call** (original: 1/2). Some of their words occur in the samples ("plan", "side",
  "first", "year"), so retrieval sends excerpts and the model has to say "not found" itself.

Files: `retrieval-pre-audit-20261026-102514.json`, `retrieval-hard-pre-audit-20261026-102524.json`,
`retrieval-heldout-pre-audit-20261026-102529.json`, `retrieval-heldout-hard-pre-audit-20261026-102534.json` (before);
`retrieval-20261026-103222.json`, `retrieval-hard-20261026-103227.json`, `retrieval-heldout-20261026-103231.json`,
`retrieval-heldout-hard-20261026-103235.json` (after). The task-1a baseline, run before any change, is
`superseded-retrieval-20261026-101900-pre-audit-synonyms-baseline.json` and matches the "original, easy, before" row.

## 3. Quality eval: original vs held-out (live, Claude Haiku 5.5, effort low, sample transcripts)

| Questions | Full context | RAG (audited synonyms) |
|---|---|---|
| original, 10 | 10/10 (`quality-claude-haiku-5-5-20261026-103352.json`) | 10/10 (`quality-rag-claude-haiku-5-5-20261026-103439.json`); 10/10 before the audit (`superseded-quality-rag-claude-haiku-5-5-20261025-221819-pre-audit-synonyms.json`) |
| held-out, 12 | **10/12** (`quality-heldout-claude-haiku-5-5-20261026-103421.json`) | **9/12** (`quality-rag-heldout-claude-haiku-5-5-20261026-103505.json`) |

- **RAG held-out failures** (`ho-outside-testers`, `ho-night-theme`, `ho-compliance-paperwork`) are exactly the three
  questions RET-01 could not retrieve at S. The answer turn never reached the model, so it said "not found". This is the
  failure mode the README's limitations section warns about.
- **Full-context held-out failures** (`ho-go-out-day`, `ho-night-theme`): the model found the right facts (November 14th
  and the payment integration; dark mode, three days, version two) but opened with "Not found in the transcripts"
  because the paraphrase did not match the transcript's words, and it put the question's own words in quotation marks
  ("site rebuild", "low-light colour scheme"). The citation checker flagged those quoted words as quotes that are not in
  the cited turn, which failed the "no invented quotes" check. The answers are recorded as failures, unedited. Both the
  hedging and the checker's treatment of quoted question words are open issues, not fixed here.

Live spend for these four runs: $0.0019 + $0.0024 + $0.0035 + $0.0045 = $0.0123.

## What this does and does not show

- The original ten questions remain a regression baseline on a development set. They are not evidence of generalisation.
- The held-out set was written by a model (Claude Code) about the same two fictional calls. It is better than nothing and
  it exposed real misses, but a held-out set written by a human reviewer, ideally on real transcripts, is still needed.
- n is small: 8 to 10 answerable questions per row, so one question moves a rate by 10 to 13 points.
- Not fixed, found while doing this: `expect_files: ["planning"]` matches any file name containing "planning", and the
  original easy filler includes "Event planning", "Office move planning" and "Newsletter planning" meetings, so in the
  operational runs at M, L and XL an answer citing one of those would satisfy that check. The hard filler avoids those
  words. Changing the check would change the expected answers, so it is reported here instead.

## Reproduce

```
python evals/retrieval_eval.py --questions evals/questions.yaml --distractors easy --synonyms pre-audit
python evals/retrieval_eval.py --questions evals/heldout/questions.yaml --distractors hard
python evals/run_eval.py --engine rag --questions evals/heldout/questions.yaml --dry-run
```

RET-01 is free and deterministic. The live quality eval varies from run to run (see the README's note on sampling).
