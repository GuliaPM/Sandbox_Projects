"""Parse Otter.ai .txt exports into meetings and turns, render them for the model,
and check the model's citations against the source text."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# "Speaker 1  00:49" alone on a line, right after a blank line (or at the top of the file).
_HEADER_RE = re.compile(r"^(?P<speaker>[^\s.?!,;][^?!,;]{0,39}?)\s+(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)$")
_FOOTER_RE = re.compile(r"^transcribed by\b.*otter\.ai", re.IGNORECASE)

# [Meeting title, Speaker 2, 05:10]  (a "|" separator is accepted too)
CITE_RE = re.compile(
    r"\[([^\[\]\n]*?)\s*[,|]\s*([^,|\[\]\n]+?)\s*[,|]\s*(\d{1,2}:\d{2}(?::\d{2})?)\s*\]"
)
_MULTI_CITE_RE = re.compile(r"\[([^\[\]\n]*?\d{1,2}:\d{2}(?::\d{2})?\s*;[^\[\]\n]*)\]")
_QUOTE_RE = re.compile(r'["“]([^"“”]+?)["”]')  # one-letter quotes like "T" must pair up too, or they shift every later pair
_ELLIPSIS_RE = re.compile(r"\.\.\.|…")


def split_multi_citations(text: str) -> str:
    """The model sometimes puts two tags in one bracket: [A, Speaker 1, 05:10; A, Speaker 2, 06:00].
    Rewrite that as [A, Speaker 1, 05:10], [A, Speaker 2, 06:00] so each one is checked and shown."""
    def fix(m: re.Match) -> str:
        parts = [p.strip() for p in m.group(1).split(";")]
        if all(CITE_RE.fullmatch(f"[{p}]") for p in parts):
            return ", ".join(f"[{p}]" for p in parts)
        return m.group(0)
    return _MULTI_CITE_RE.sub(fix, text)


def ts_to_seconds(ts: str) -> int:
    secs = 0
    for part in ts.split(":"):
        secs = secs * 60 + int(part)
    return secs


def _norm(text: str) -> str:
    """Lowercase, drop punctuation, and collapse immediately repeated words.

    Otter transcribes stutters ("our our conversation"); the model tends to clean them up when
    quoting, so matching ignores adjacent duplicates on both sides.
    """
    text = text.lower().replace("’", "'").replace("‘", "'")
    out: list[str] = []
    for tok in re.sub(r"[^\w']+", " ", text).split():
        tok = tok.strip("'")  # keep "we've", drop the single quotes of a quote nested in a quote
        if tok and (not out or out[-1] != tok):
            out.append(tok)
    return " ".join(out)


@dataclass
class Turn:
    idx: int
    speaker: str  # label as written in the file, e.g. "Speaker 2"
    label: str  # label shown to the model (adds a name from meetings.yaml if given)
    start: str  # timestamp as written in the file
    seconds: int
    text: str
    norm: str = field(default="", repr=False)
    numbers: tuple | None = field(default=None, repr=False, compare=False)  # (numbers, months), filled in by guardrails

    def __post_init__(self) -> None:
        self.norm = _norm(self.text)


@dataclass
class Meeting:
    id: str
    title: str
    date: str | None
    path: Path
    turns: list[Turn]
    warnings: list[str] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return sum(len(t.text.split()) for t in self.turns)

    @property
    def speakers(self) -> list[str]:
        seen: list[str] = []
        for t in self.turns:
            if t.label not in seen:
                seen.append(t.label)
        return seen


@dataclass
class LoadResult:
    meetings: list[Meeting]
    ignored: list[str]  # files that are not transcripts (e.g. the .mp4)
    failed: list[tuple[str, str]]  # (file, reason) for .txt files that did not parse
    folder_missing: bool = False


# --------------------------------------------------------------------------- parsing


def parse_turns(raw: str, speaker_names: dict[str, str] | None = None) -> tuple[list[Turn], list[str]]:
    speaker_names = speaker_names or {}
    warnings: list[str] = []
    drafts: list[dict] = []
    preamble: list[str] = []
    prev_blank = True
    last_secs = -1
    for line in raw.splitlines():
        s = line.strip()
        if _FOOTER_RE.match(s):
            if drafts:
                drafts[-1]["closed"] = True  # nothing after the footer belongs to the last turn
            prev_blank = False
            continue
        header = _HEADER_RE.match(s) if (prev_blank and s) else None
        if header:
            speaker, ts = header["speaker"].strip(), header["ts"]
            secs = ts_to_seconds(ts)
            if secs < last_secs:
                warnings.append(f"timestamp goes backwards at {ts} ({speaker}); check how this file was split")
            last_secs = secs
            name = speaker_names.get(speaker)
            drafts.append(
                {"speaker": speaker, "label": f"{speaker} ({name})" if name else speaker,
                 "start": ts, "seconds": secs, "parts": []}
            )
        elif s and drafts and not drafts[-1].get("closed"):
            drafts[-1]["parts"].append(s)
        elif s and not drafts:
            preamble.append(s)
        prev_blank = not s
    if preamble:
        warnings.append(f"{len(preamble)} line(s) before the first speaker line were ignored (first: {preamble[0][:60]!r})")
    turns: list[Turn] = []
    for d in drafts:
        text = " ".join(d["parts"]).strip()
        if not text:
            warnings.append(f"empty turn at {d['start']} ({d['speaker']}) skipped")
            continue
        turns.append(Turn(len(turns), d["speaker"], d["label"], d["start"], d["seconds"], text))
    return turns, warnings


def title_from_filename(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[_\s]*transcript$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"\bRecording\b", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"\s+", " ", stem.replace("_", " ")).strip()
    return stem or Path(name).stem


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "meeting"


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _load_meta(meta_path: Path | None) -> dict:
    if not meta_path or not meta_path.is_file():
        return {}
    try:
        data = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def load_meetings(folder: Path, meta_path: Path | None = None) -> LoadResult:
    result = LoadResult([], [], [])
    if not folder.is_dir():
        result.folder_missing = True
        return result
    meta = _load_meta(meta_path)
    used_titles: set[str] = set()
    for path in sorted((p for p in folder.iterdir() if p.is_file()), key=lambda p: p.name.lower()):
        if path.suffix.lower() != ".txt":
            result.ignored.append(path.name)
            continue
        try:
            raw = _read_text(path)
        except OSError as exc:
            result.failed.append((path.name, f"could not read file ({exc.strerror or exc})"))
            continue
        derived = title_from_filename(path.name)
        info = meta.get(path.name) or meta.get(path.stem) or meta.get(derived) or {}
        turns, warnings = parse_turns(raw, info.get("speakers") or {})
        if not turns:
            result.failed.append((path.name, "no 'Speaker N  mm:ss' lines found, so this does not look like an Otter transcript"))
            continue
        title = str(info.get("title") or derived)
        base, n = title, 2
        while title.lower() in used_titles:
            title, n = f"{base} ({n})", n + 1
        used_titles.add(title.lower())
        date = info.get("date")
        result.meetings.append(
            Meeting(_slug(title), title, str(date) if date else None, path, turns, warnings)
        )
    return result


# --------------------------------------------------------------------------- rendering


SEGMENT_WORDS = 90  # a long turn is shown as several lines of about this many words, each with the turn's tag


def _segments(text: str, target: int = SEGMENT_WORDS) -> list[str]:
    """Split a long turn at sentence ends. Otter turns can run 1,000 words; repeating the tag every
    few sentences stops the model attributing the end of a turn to the line above it."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out: list[str] = []
    current: list[str] = []
    count = 0
    for s in sentences:
        current.append(s)
        count += len(s.split())
        if count >= target:
            out.append(" ".join(current))
            current, count = [], 0
    if current:
        out.append(" ".join(current))
    return out


def render_corpus(meetings: list[Meeting]) -> str:
    """One stable text block: one line per turn (long turns: one line per ~90 words, same tag).
    Must stay byte-identical between calls (it sits in front of the prompt-cache breakpoint)."""
    blocks = []
    for m in meetings:
        # The prefix is exactly the citation format, so the model can copy it instead of rebuilding it.
        lines = [f"[{m.title}, {t.label}, {t.start}] {seg}" for t in m.turns for seg in _segments(t.text)]
        blocks.append(f"=== MEETING: {m.title} | date: {m.date or 'unknown'} ===\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def excerpt(meeting: Meeting, idx: int, around: int = 2) -> list[Turn]:
    return meeting.turns[max(0, idx - around): idx + around + 1]


# --------------------------------------------------------------------------- citation check


@dataclass
class QuoteCheck:
    text: str
    status: str  # "near" (in the cited turn) | "elsewhere" | "missing" | "skipped"
    where: str = ""  # human-readable location when found elsewhere
    meeting: Meeting | None = None  # where an "elsewhere" quote actually is
    turn_idx: int | None = None
    matched: CitationCheck | None = None  # the citation whose turn holds a "near" quote


@dataclass
class CitationCheck:
    raw: str
    title: str
    speaker: str
    ts: str
    meeting: Meeting | None = None
    turn_idx: int | None = None
    found_idx: int | None = None  # turn that really holds the quoted words when the cited timestamp is off
    status: str = "ok"  # "ok" | "weak" | "bad"
    problems: list[str] = field(default_factory=list)
    quotes: list[QuoteCheck] = field(default_factory=list)

    def _flag(self, status: str, problem: str) -> None:
        self.problems.append(problem)
        if status == "bad" or self.status == "ok":
            self.status = status


@dataclass
class CheckReport:
    citations: list[CitationCheck] = field(default_factory=list)
    uncited_quotes: list[QuoteCheck] = field(default_factory=list)
    numbers: list = field(default_factory=list)  # guardrails.NumberFinding, added by guardrails.check_numbers

    @property
    def numbers_flagged(self) -> list:
        """The numbers and dates in the answer that are not in the turn they are cited to."""
        return [n for n in self.numbers if n.status != "ok"]

    @property
    def counts(self) -> dict[str, int]:
        out = {"ok": 0, "weak": 0, "bad": 0}
        for c in self.citations:
            out[c.status] += 1
        return out

    @property
    def clean(self) -> bool:
        c = self.counts
        return c["weak"] == 0 and c["bad"] == 0 and not any(q.status == "missing" for q in self.uncited_quotes)


def _norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def _find_meeting(meetings: list[Meeting], title: str) -> Meeting | None:
    want = _norm_title(title)
    exact = [m for m in meetings if _norm_title(m.title) == want]
    if exact:
        return exact[0]
    loose = [m for m in meetings if want and (want in _norm_title(m.title) or _norm_title(m.title) in want)]
    return loose[0] if len(loose) == 1 else None


def _speaker_matches(cited: str, turn: Turn) -> bool:
    c = re.sub(r"\s*\(.*?\)\s*$", "", cited).strip().lower()  # "Speaker 2 (me)" -> "speaker 2"
    return c in (turn.speaker.lower(), turn.label.lower())


def _resolve(match: re.Match, meetings: list[Meeting]) -> CitationCheck:
    title, speaker, ts = (g.strip() for g in match.groups())
    chk = CitationCheck(match.group(0), title, speaker, ts)
    chk.meeting = _find_meeting(meetings, title)
    if chk.meeting is None:
        chk._flag("bad", f"no meeting called '{title}'")
        return chk
    secs = ts_to_seconds(ts)
    exact = [t for t in chk.meeting.turns if t.seconds == secs]
    same_speaker = [t for t in exact if _speaker_matches(speaker, t)]
    if same_speaker:
        chk.turn_idx = same_speaker[0].idx
    elif exact:
        chk.turn_idx = exact[0].idx
        chk._flag("weak", f"{ts} is a turn by {exact[0].label}, not {speaker}")
    else:
        nearest = min(chk.meeting.turns, key=lambda t: abs(t.seconds - secs))
        if abs(nearest.seconds - secs) <= 15:
            chk.turn_idx = nearest.idx
            chk._flag("weak", f"no turn starts at {ts}; using the nearest one at {nearest.start}")
        else:
            chk._flag("bad", f"no turn near {ts} in '{chk.meeting.title}'")
    return chk


def _quotes(segment: str) -> list[str]:
    return [m.group(1).strip() for m in _QUOTE_RE.finditer(segment)]


def _contains(haystack: str, frag: str) -> bool:
    """Whole-word match. Long fragments may also start or end mid-word (a quote that begins at "'ll be"
    inside "it'll be"); short ones may not, or "is a" would match inside "this and"."""
    if f" {frag} " in f" {haystack} ":
        return True
    return len(frag) >= 12 and frag in haystack


def _in_turn(turns: list[Turn], idx: int, frag: str) -> bool:
    """True if the fragment is inside turn `idx`, or runs across its boundary with the next or previous turn.
    Otter sometimes ends a turn mid-sentence ("approved by" | "you, yeah agree"), so one quote can span two."""
    if _contains(turns[idx].norm, frag):
        return True
    for a, b in ((idx - 1, idx), (idx, idx + 1)):
        if a >= 0 and b < len(turns):
            if (_contains(f"{turns[a].norm} {turns[b].norm}", frag)
                    and not _contains(turns[a].norm, frag) and not _contains(turns[b].norm, frag)):
                return True
    return False


def _locate_quote(quote: str, group: list[CitationCheck], meetings: list[Meeting], min_words: int = 2) -> QuoteCheck:
    # Names of meetings and speakers are put in quotation marks by the model sometimes; they are not quotes.
    names = {_norm(m.title) for m in meetings} | {_norm(t.label) for m in meetings for t in m.turns} \
        | {_norm(t.speaker) for m in meetings for t in m.turns}
    fragments = [f for f in (_norm(p) for p in _ELLIPSIS_RE.split(quote))
                 if len(f.split()) >= min_words and f not in names]
    if not fragments:
        return QuoteCheck(quote, "skipped")
    worst, where, loc, matched = "near", "", None, None
    for frag in fragments:
        near = False
        for c in group:
            # Only the cited turn counts: a quote from the turn next to it is a wrong citation, and the
            # reader would not see the quote in the excerpt for the citation.
            if c.meeting is not None and c.turn_idx is not None:
                if _in_turn(c.meeting.turns, c.turn_idx, frag):
                    near = True
                    matched = matched or c
                    break
        if near:
            continue
        found = None
        for m in meetings:
            hit = next((t for t in m.turns if _in_turn(m.turns, t.idx, frag)), None)
            if hit:
                found = (m, hit)
                break
        if found:
            if worst == "near":
                worst, where, loc = "elsewhere", f"{found[0].title}, {found[1].label}, {found[1].start}", found
        else:
            worst, where, loc = "missing", "", None
            break
    return QuoteCheck(quote, worst, where, loc[0] if loc else None, loc[1].idx if loc else None,
                      matched if worst == "near" else None)


def check_citations(answer: str, meetings: list[Meeting]) -> CheckReport:
    """Check each citation, and each quote against the citations next to it on the same line.

    The model writes either `"quote" [cite]` or `Speaker 2 [cite] said "quote"`, so a quote is compared with
    the nearest citation after it and the nearest before it; it is fine if it is in either one's turn."""
    report = CheckReport()
    matches = list(CITE_RE.finditer(answer))
    checks = [_resolve(m, meetings) for m in matches]
    report.citations = checks

    # Consecutive citations with nothing but separators between them act as one: [A], [B]
    groups: list[list[int]] = []
    for i, m in enumerate(matches):
        if groups and re.fullmatch(r"[\s,;]*", answer[matches[groups[-1][-1]].end(): m.start()]):
            groups[-1].append(i)
        else:
            groups.append([i])
    spans = [(matches[g[0]].start(), matches[g[-1]].end()) for g in groups]

    pos = 0
    for line in answer.split("\n"):  # per line, so one stray quotation mark cannot upset the rest of the answer
        line_start, line_end = pos, pos + len(line)
        pos = line_end + 1
        for qm in _QUOTE_RE.finditer(line):
            quote, qs, qe = qm.group(1).strip(), line_start + qm.start(), line_start + qm.end()
            before = next((i for i in reversed(range(len(groups))) if line_start <= spans[i][0] and spans[i][1] <= qs), None)
            after = next((i for i in range(len(groups)) if qe <= spans[i][0] and spans[i][1] <= line_end), None)
            neighbours = [groups[i] for i in (after, before) if i is not None]  # the one after wins ties
            if not neighbours:
                # Short uncited quotes are usually search terms ("SOC 2"), not something a speaker is said to have said.
                res = _locate_quote(quote, [], meetings, min_words=4)
                if res.status != "skipped":
                    report.uncited_quotes.append(res)
                continue
            candidates = [checks[i] for g in neighbours for i in g]
            res = _locate_quote(quote, candidates, meetings)
            if res.status == "skipped":
                continue
            owner = res.matched or checks[neighbours[0][0]]
            owner.quotes.append(res)
            if res.status == "missing":
                owner._flag("bad", f'quote not found in any transcript: "{quote[:70]}"')
            elif res.status == "elsewhere":
                owner._flag("weak", f'quote is from a different place ({res.where}): "{quote[:70]}"')
                if owner.found_idx is None and res.meeting is owner.meeting:
                    owner.found_idx = res.turn_idx
    return report
