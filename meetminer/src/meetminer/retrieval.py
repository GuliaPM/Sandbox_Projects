"""The RAG engine's retrieval step: find the passages of the transcripts that matter for a question.

Everything happens on this PC and in memory. The index is built from the loaded transcripts when the first RAG question
is asked (a few milliseconds for hundreds of turns), is never written to disk, and is rebuilt when the transcripts are
reloaded, so deleting a transcript removes it from retrieval too. No embedding service is involved: ranking is BM25, the
keyword-relevance formula search engines have used for decades, with light stemming and a short list of synonyms.

What is sent to Claude is only the retrieved turns (with one turn either side, so a question and its answer stay
together), in the same tagged-line format as the full-context engine, so the citation and numbers checks work unchanged.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .transcripts import Meeting, Turn, _segments

STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be because been before being below between both but by
can could did do does doing down during each few for from further had has have having he her here hers him his how i if
in into is it its itself just let me more most my no nor not now of off on once only or other our ours out over own same
she should so some such than that the their theirs them then there these they this those through to too under until up
us very was we were what when where which while who whom why will with would you your yours yeah okay ok um uh like
really get got go going well think know right said say says thing things lot
""".split())

# Words a person asks with that the transcript may say differently. Applied to the question only, at half weight.
SYNONYMS = {
    "price": ["cost", "pricing", "fee", "per"], "cost": ["price", "pricing", "spend"], "fee": ["price", "cost"],
    "document": ["report", "agreement", "contract", "paperwork"], "documents": ["report", "agreement", "contract"],
    "deadline": ["due", "date"], "due": ["deadline", "date"], "when": ["date", "day"],
    "budget": ["money", "spend", "dollars"], "money": ["budget", "dollars"],
    "action": ["task", "follow"], "owner": ["own", "responsible"], "owns": ["own", "owner", "deliver"],
    "decided": ["decision", "agreed", "agree"], "decision": ["decided", "agreed"],
    "security": ["soc", "compliance", "review"], "launch": ["release", "ship", "live"],
    "customers": ["users", "clients"], "invitations": ["invites", "invite"], "signing": ["sign", "signature"],
    "discount": ["prepay", "off"], "meeting": ["call"], "call": ["meeting"],
}
_TOKEN = re.compile(r"[a-z0-9]+(?:[.,'][a-z0-9]+)*")
_SUFFIXES = ("ations", "ation", "ments", "ment", "ings", "ing", "edly", "ies", "ied", "es", "ed", "ly", "s")


def stem(word: str) -> str:
    """A deliberately small stemmer: "invitations", "invites" and "invite" all become "invit"."""
    w = word
    for suffix in _SUFFIXES:
        if w.endswith(suffix) and len(w) > len(suffix) + 2:
            if suffix == "s" and w.endswith("ss"):
                break
            w = w[: -len(suffix)] + ("y" if suffix in ("ies", "ied") else "")
            break
    if len(w) > 3 and w.endswith("e"):
        w = w[:-1]
    return w


def tokenize(text: str) -> list[str]:
    out = []
    for raw in _TOKEN.findall(text.lower().replace("’", "'")):
        raw = raw.split("'")[0] if "'" in raw else raw
        if re.fullmatch(r"[\d.,]+", raw):
            out.append(raw.replace(",", "").rstrip("."))  # $18,000 and 18000 are the same number
        elif raw not in STOPWORDS and len(raw) > 1:
            out.append(stem(raw))
    return out


@dataclass
class Chunk:
    meeting: Meeting
    turn: Turn
    text: str
    terms: Counter


@dataclass
class Retrieved:
    """What one question retrieved, and the text that goes to Claude."""
    text: str = ""
    turns: list[tuple[Meeting, Turn]] = field(default_factory=list)  # in the order they are shown
    hits: list[tuple[float, Chunk]] = field(default_factory=list)  # the best-matching chunks, best first
    tokens_estimate: int = 0

    @property
    def meetings(self) -> list[Meeting]:
        seen: list[Meeting] = []
        for m, _ in self.turns:
            if not any(m is s for s in seen):
                seen.append(m)
        return seen

    def holds(self, meeting_title: str, start: str) -> bool:
        return any(m.title == meeting_title and t.start == start for m, t in self.turns)


class BM25Index:
    def __init__(self, meetings: list[Meeting], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.chunks: list[Chunk] = []
        for m in meetings:
            title_terms = tokenize(m.title)  # "the planning call" should find the planning call
            for t in m.turns:
                for seg in _segments(t.text):
                    self.chunks.append(Chunk(m, t, seg, Counter(tokenize(seg) + title_terms)))
        self.avg_len = sum(sum(c.terms.values()) for c in self.chunks) / max(len(self.chunks), 1)
        df: Counter = Counter()
        for c in self.chunks:
            df.update(c.terms.keys())
        n = len(self.chunks)
        self.idf = {term: math.log(1 + (n - f + 0.5) / (f + 0.5)) for term, f in df.items()}

    def score(self, query: dict[str, float], chunk: Chunk) -> float:
        length = sum(chunk.terms.values())
        total = 0.0
        for term, weight in query.items():
            tf = chunk.terms.get(term, 0)
            if tf:
                norm = tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * length / max(self.avg_len, 1)))
                total += weight * self.idf.get(term, 0.0) * norm
        return total

    def search(self, query: dict[str, float]) -> list[tuple[float, Chunk]]:
        scored = [(self.score(query, c), c) for c in self.chunks]
        return sorted([s for s in scored if s[0] > 0], key=lambda s: -s[0])


def build_query(question: str, history: list[tuple[str, str]] | None = None) -> dict[str, float]:
    """Terms and weights: the question's own words at 1, their synonyms and the previous question's words at 0.5
    (so a follow-up such as "and when is it due?" still finds what the last question was about)."""
    query: dict[str, float] = defaultdict(float)
    for term in tokenize(question):
        query[term] = max(query[term], 1.0)
    for word in re.findall(r"[a-z]+", question.lower()):
        for alt in SYNONYMS.get(word, []):
            for term in tokenize(alt):
                query[term] = max(query[term], 0.5)
    if history:
        for term in tokenize(history[-1][0]):
            query[term] = max(query[term], 0.5)
    return dict(query)


class Retriever:
    """Picks the turns to send for one question: the best `top_k` chunks, the turns either side of each, and the best
    chunk of every other meeting that matches well (so a question comparing meetings sees all of them), within a budget
    of about `max_tokens` tokens."""

    def __init__(self, meetings: list[Meeting], top_k: int = 12, neighbors: int = 1, max_tokens: int = 6000):
        self.meetings = meetings
        self.index = BM25Index(meetings)
        self.top_k, self.neighbors, self.max_tokens = top_k, neighbors, max_tokens

    def retrieve(self, question: str, history: list[tuple[str, str]] | None = None) -> Retrieved:
        hits = self.index.search(build_query(question, history))
        if not hits:
            return Retrieved()
        wanted: list[tuple[Meeting, int]] = []
        keys: set[tuple[int, int]] = set()  # meetings are compared by identity: two calls can look alike

        def want(meeting: Meeting, idx: int) -> None:
            if 0 <= idx < len(meeting.turns) and (id(meeting), idx) not in keys:
                keys.add((id(meeting), idx))
                wanted.append((meeting, idx))

        for _, chunk in hits[: self.top_k]:
            want(chunk.meeting, chunk.turn.idx)
            for d in range(1, self.neighbors + 1):
                want(chunk.meeting, chunk.turn.idx - d)
                want(chunk.meeting, chunk.turn.idx + d)
        best_score = hits[0][0]
        covered = {id(m) for m, _ in wanted}
        for score, chunk in hits:  # every other meeting that matches well enough gets its best passage
            if id(chunk.meeting) not in covered and score >= 0.3 * best_score:
                want(chunk.meeting, chunk.turn.idx)
                covered.add(id(chunk.meeting))

        kept: list[tuple[Meeting, int]] = []
        used = 0
        for meeting, idx in wanted:  # in order of importance, until the budget is spent
            cost = sum(len(seg) // 4 + 12 for seg in _segments(meeting.turns[idx].text))
            if kept and used + cost > self.max_tokens:
                continue
            kept.append((meeting, idx))
            used += cost
        return Retrieved(text=render_excerpts(self.meetings, kept), tokens_estimate=used,
                         turns=[(m, m.turns[i]) for m in self.meetings for i in sorted(i for mm, i in kept if mm is m)],
                         hits=hits[: self.top_k])


def render_excerpts(meetings: list[Meeting], kept: list[tuple[Meeting, int]]) -> str:
    """The chosen turns in the full-context engine's line format, meetings in their usual order, turns in time order,
    with a marker where turns were left out."""
    blocks = []
    for m in meetings:
        idxs = sorted(i for mm, i in kept if mm is m)
        if not idxs:
            continue
        lines, last = [], None
        for i in idxs:
            if last is not None and i != last + 1:
                lines.append("[... turns not shown ...]")
            t = m.turns[i]
            lines += [f"[{m.title}, {t.label}, {t.start}] {seg}" for seg in _segments(t.text)]
            last = i
        blocks.append(f"=== MEETING: {m.title} | date: {m.date or 'unknown'} | excerpts ===\n" + "\n".join(lines))
    return "\n\n".join(blocks)
