# GR-RAG-01: no match, no call; the RAG index stays in memory

```text
GUARDRAIL ID:      GR-RAG-01                  NAME: No match, no call                      STATUS: in place (1.2.0)
AREA:              cost, privacy, integrity   TYPE: preventive       LAYER: code
SEVERITY IF MISSING: medium                   OWNER: maintainer

RISK ADDRESSED:    With the RAG engine, a question whose words appear nowhere in the transcripts ("Was Kubernetes
                   discussed?") would otherwise send an empty or irrelevant context to Claude: money spent on a call that
                   cannot find anything, and an invitation to answer from general knowledge. A RAG index written to disk
                   would also be one more copy of the transcripts to protect, and one that deleting a transcript would
                   not clean up.
TRIGGER:           A RAG question for which BM25 retrieval scores every passage at zero (not one of its words, their
                   stems or the listed synonyms occurs in any transcript).
ACTION:            block the call. The answer is "Not found in the transcripts. No passage matched the words in your
                   question, so nothing was sent to Claude. Try other words, or switch to Full context." It costs $0,
                   is logged with error_kind "no_match", and is counted separately on the RAG reports page.
                   The index is built in memory when the first RAG question is asked, rebuilt whenever the transcripts
                   are reloaded, and never written to disk.
USER MESSAGE:      as above; the line under the answer says "RAG · no passage matched, nothing was sent · $0.0000".
CONFIG:            none (always on in the RAG engine). The engine itself is chosen with the switch in the sidebar
                   (engine in settings.json; default "full" in config/default.yaml).
CODE:              MeetingQA.ask and the NO_MATCH text in src/meetminer/engine.py; Retriever.retrieve in
                   src/meetminer/retrieval.py; summarize_usage in src/meetminer/reports.py.
TEST (must exist): tests/test_rag.py: a question with no word in the transcripts retrieves nothing; the engine answers
                   "not found" without calling the API and logs $0 under "rag"; the index never touches the disk;
                   new transcripts rebuild the index. tests/test_app.py: in the window, such a question costs nothing.
MONITOR:           RAG reports page: "N questions matched no passage". RET-01 (evals/retrieval_eval.py) reports how many
                   not-in-the-transcripts questions end without a call, per transcript size.
KNOWN BYPASS / LIMITS:
  - Keyword retrieval matches words, not meaning. A question worded unlike the transcript ("how much is each user?"
    against "per seat") can match nothing and be answered "not found" when the answer is there. The message says to try
    other words or switch to full context, and the synonym list covers common cases; embeddings are the real fix.
  - The opposite also happens: a not-in-the-transcripts question that shares ordinary words ("competitor product")
    retrieves something, and then it is the model's "not found" rule that has to hold (it did, in the evals).
  - "In memory" protects against stale copies on disk, not against anything that can read the app's memory.
REVIEW DATE:       when embeddings or a persistent index are added
```
