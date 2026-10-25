"""Guardrails that run inside the app. Each one has a spec in the repository's guardrails/ folder (same ID).

    GR-COST-05  daily and monthly spending limits, read from usage.csv so they survive a restart
    GR-COST-06  the cost multiple shown before switching to a more expensive model
    GR-INT-04   numbers and dates in an answer are checked against the turn they are cited to

GR-REL-03 (the request timeout) lives in engine.py, and GR-INJ-03 (the prompt-injection probes) is an eval in evals/.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from . import usagelog
from .transcripts import CITE_RE, CheckReport, CitationCheck, Meeting

# --------------------------------------------------------------------------- GR-COST-05: daily and monthly limits


@dataclass
class SpendStatus:
    today: float
    month: float
    daily_limit: float  # 0 means no limit
    monthly_limit: float

    @staticmethod
    def _share(spent: float, limit: float) -> float:
        return spent / limit if limit > 0 else 0.0

    @property
    def daily_share(self) -> float:
        return self._share(self.today, self.daily_limit)

    @property
    def monthly_share(self) -> float:
        return self._share(self.month, self.monthly_limit)

    @property
    def daily_reached(self) -> bool:
        return self.daily_limit > 0 and self.today >= self.daily_limit

    @property
    def monthly_reached(self) -> bool:
        return self.monthly_limit > 0 and self.month >= self.monthly_limit


def spend_status(settings, now: datetime | None = None) -> SpendStatus:
    """What has been spent today and this calendar month (local time), summed from the usage log."""
    now = now or datetime.now()
    today = month = 0.0
    for row in usagelog.read_rows(settings.usage_log):
        when = row["time"]
        if when.year == now.year and when.month == now.month:
            month += row["cost_usd"]
            if when.day == now.day:
                today += row["cost_usd"]
    return SpendStatus(today, month, float(getattr(settings, "max_daily_cost_usd", 0) or 0),
                       float(getattr(settings, "max_monthly_cost_usd", 0) or 0))


def spend_limit_error(settings, now: datetime | None = None) -> tuple[str, str] | None:
    """(error_kind, message) when today's or this month's limit has been reached, otherwise None."""
    status = spend_status(settings, now)
    if status.daily_reached:
        return ("daily_cap", f"Daily spending limit reached (${status.today:.2f} of ${status.daily_limit:.2f} today). "
                             "Raise the daily limit in Settings, or try again tomorrow.")
    if status.monthly_reached:
        return ("monthly_cap", f"Monthly spending limit reached (${status.month:.2f} of ${status.monthly_limit:.2f} "
                               "this month). Raise the monthly limit in Settings, or wait for next month.")
    return None


# --------------------------------------------------------------------------- GR-COST-06: model-switch confirmation

# What a typical question looks like, for the estimate: the transcripts come from the cache, plus a short new question
# and a medium-sized answer.
TYPICAL_NEW_INPUT = 150
TYPICAL_OUTPUT = 400
DEFAULT_CORPUS_TOKENS = 4000


@dataclass
class ModelComparison:
    old_model: str
    new_model: str
    old_warm: float  # USD for one typical question while the prompt cache is warm
    new_warm: float
    old_cold: float  # USD for the first question after the cache expired (five idle minutes)
    new_cold: float

    @property
    def multiple(self) -> float:
        return self.new_warm / self.old_warm if self.old_warm > 0 else float("inf")

    @property
    def more_expensive(self) -> bool:
        return self.new_warm > self.old_warm * 1.05


def compare_models(price_table: dict, old_model: str, new_model: str, cache_ttl: str,
                   corpus_tokens: int = 0) -> ModelComparison | None:
    """None when either model has no price entry (nothing sensible to say, so no confirmation is asked)."""
    from .engine import ConfigError, cost_usd, resolve_prices  # engine imports this module, so import it late

    corpus_tokens = corpus_tokens or DEFAULT_CORPUS_TOKENS
    try:
        old, new = resolve_prices(price_table, old_model, cache_ttl), resolve_prices(price_table, new_model, cache_ttl)
    except ConfigError:
        return None
    warm = {"input": TYPICAL_NEW_INPUT, "cache_write": 0, "cache_read": corpus_tokens, "output": TYPICAL_OUTPUT}
    cold = {"input": TYPICAL_NEW_INPUT, "cache_write": corpus_tokens, "cache_read": 0, "output": TYPICAL_OUTPUT}
    return ModelComparison(old_model, new_model, cost_usd(warm, old), cost_usd(warm, new),
                           cost_usd(cold, old), cost_usd(cold, new))


# --------------------------------------------------------------------------- GR-INT-04: numbers and dates

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
             "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
             "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
             "twentieth": 20, "thirtieth": 30, "fortieth": 40, "fiftieth": 50}
_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
_WORD = "|".join(sorted([*_UNITS, *_TENS, *_ORDINALS, *_SCALES], key=len, reverse=True))
_WORD_SEQ = re.compile(rf"\b(?:{_WORD})(?:[\s-]+(?:and[\s-]+)?(?:{_WORD}))*\b", re.IGNORECASE)
# A number may be followed by a colon ("$12,000: ...") but not by a colon and a digit, which is a time (04:09).
_NUM_RE = re.compile(
    r"(?<![\w.:])\$?\d[\d,]*(?:\.\d+)?(?:\s?(?:thousand|million|billion|k|m|b|%|st|nd|rd|th))?(?!\w|:\d)", re.IGNORECASE)
_GAP = re.compile(r"[\s,;]*(?:\b(?:and|or)\b|&|\+)?[\s,;]*")  # what may sit between two citations that belong together
_SCALE_SUFFIX = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}

_MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                "november", "december"]
_MONTH_PATTERN = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
                  r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_DATE_A = re.compile(rf"\b(?P<m>{_MONTH_PATTERN})\.?\s+(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\b", re.IGNORECASE)
_DATE_B = re.compile(rf"\b(?P<d>\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<m>{_MONTH_PATTERN})\b", re.IGNORECASE)
_BARE_MONTH = re.compile(r"\b(" + "|".join(m for m in _MONTH_NAMES if m != "may") + r")\b", re.IGNORECASE)
_LIST_MARKER = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)")
_SPEAKER = re.compile(r"\bspeaker\s+\d+\b", re.IGNORECASE)


def _month_number(name: str) -> int:
    key = name.lower().rstrip(".")[:3]
    return next(i + 1 for i, full in enumerate(_MONTH_NAMES) if full.startswith(key))


def _parse_words(match: re.Match) -> list[int]:
    """The number or numbers spelled out in one run of number words ("eighteen thousand" -> [18000])."""
    out: list[int] = []
    total = current = 0
    last = ""
    started = False

    def flush() -> None:
        nonlocal total, current, last, started
        if started:
            out.append(total + current)
        total = current = 0
        last, started = "", False

    for raw in re.split(r"[\s-]+", match.group(0).lower()):
        if raw == "and":
            continue
        if raw in _UNITS or raw in _ORDINALS:
            value = _UNITS.get(raw, _ORDINALS.get(raw, 0))
            kind = "unit" if value < 10 else "teen" if value < 20 else "tens"
        elif raw in _TENS:
            value, kind = _TENS[raw], "tens"
        else:
            value, kind = _SCALES[raw], "scale"
        if kind == "scale":
            if value == 100:
                current = (current or 1) * 100 if current < 100 else current
                started, last = True, "hundred"
            else:
                total += (current or 1) * value
                current, started, last = 0, True, "scale"
            continue
        if last in ("unit", "teen") or (last == "tens" and kind != "unit") or (kind == "unit" and last == "unit"):
            flush()
        current += value
        started, last = True, kind
    flush()
    return out


def _spell_digits(text: str, min_value: int) -> str:
    """Replace numbers written as words with digits, but only those worth at least `min_value`."""
    def swap(m: re.Match) -> str:
        values = _parse_words(m)
        if not values or min(values) < min_value:
            return m.group(0)
        first = re.split(r"[\s-]+", m.group(0).lower())[0]
        if first in _SCALES and re.search(r"\d\s*$", m.string[:m.start()]):
            return m.group(0)  # "1.5 million": the digits in front already make a number, _NUM_RE reads the scale word
        return " " + " ".join(str(v) for v in values) + " "
    return _WORD_SEQ.sub(swap, text)


def _canon(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.6f}".rstrip("0").rstrip(".")


def extract_numbers(text: str, words_from: int = 10) -> list[tuple[str, str]]:
    """[(as written, canonical value)] for every number in the text. Numbers spelled out in words count when they are
    at least `words_from` (the transcript side passes 0; small counts like "three" in an answer are not checked)."""
    text = _SPEAKER.sub(" ", _spell_digits(text, words_from))
    found: list[tuple[str, str]] = []
    for m in _NUM_RE.finditer(text):
        token = m.group(0)
        body = token.lstrip("$")
        match = re.match(r"([\d,]*\d(?:\.\d+)?)\s?([a-z%]*)", body, re.IGNORECASE)
        if not match:
            continue
        value = float(match.group(1).replace(",", ""))
        suffix = match.group(2).lower()
        value *= _SCALE_SUFFIX.get(suffix, 1)
        found.append((token.strip().rstrip(","), _canon(value)))
    return found


def _months_in(text: str) -> set[int]:
    months = {_month_number(m.group("m")) for rx in (_DATE_A, _DATE_B) for m in rx.finditer(text)}
    return months | {_month_number(m.group(1)) for m in _BARE_MONTH.finditer(text)}


def _turn_facts(turn) -> tuple[frozenset, frozenset]:
    if turn.numbers is None:
        nums = frozenset(canon for _, canon in extract_numbers(turn.text, words_from=0))
        turn.numbers = (nums, frozenset(_months_in(turn.text)))
    return turn.numbers


@dataclass
class NumberFinding:
    value: str  # as written in the answer, e.g. "$18,000" or "November 14"
    kind: str  # "number" | "date"
    status: str  # "ok" | "elsewhere" (in the transcripts, but not in the cited turn) | "missing" (nowhere)
    where: str = ""
    citation: CitationCheck | None = None


def _cited_turns(group: list[CitationCheck]) -> list:
    turns = []
    for c in group:
        if c.meeting is not None:
            for idx in (c.turn_idx, c.found_idx):
                if idx is not None and c.meeting.turns[idx] not in turns:
                    turns.append(c.meeting.turns[idx])
    return turns


def _locate(canon: str, meetings: list[Meeting], prefer: Meeting | None) -> str:
    """'Meeting, Speaker 2, 04:09' of a turn that holds the number, or '' when no turn does."""
    for m in ([prefer] if prefer else []) + [x for x in meetings if x is not prefer]:
        for t in m.turns:
            if canon in _turn_facts(t)[0]:
                return f"{m.title}, {t.label}, {t.start}"
    return ""


def _check_unit(text: str, group: list[CitationCheck], meetings: list[Meeting], out: list[NumberFinding]) -> None:
    text = _LIST_MARKER.sub("", text)
    for m in meetings:  # a number inside a meeting's title ("Call 2") is a name, not a claim
        text = re.sub(re.escape(m.title), " ", text, flags=re.IGNORECASE)
    turns = _cited_turns(group)
    cited_nums = set().union(*[_turn_facts(t)[0] for t in turns]) if turns else set()
    cited_months = set().union(*[_turn_facts(t)[1] for t in turns]) if turns else set()
    meeting = group[0].meeting if group else None
    lead = group[0] if group else None

    for written, canon in extract_numbers(text):
        if turns:
            if canon in cited_nums:
                out.append(NumberFinding(written, "number", "ok", citation=lead))
                continue
            where = _locate(canon, meetings, meeting)
            out.append(NumberFinding(written, "number", "elsewhere" if where else "missing", where, lead))
        else:  # no usable citation: all we can say is whether the number is in the transcripts at all
            where = _locate(canon, meetings, None)
            out.append(NumberFinding(written, "number", "ok" if where else "missing", citation=lead))

    if cited_months:
        for rx in (_DATE_A, _DATE_B):
            for dm in rx.finditer(text):
                month = _month_number(dm.group("m"))
                if month not in cited_months:
                    names = ", ".join(_MONTH_NAMES[i - 1].capitalize() for i in sorted(cited_months))
                    out.append(NumberFinding(dm.group(0).strip(), "date", "missing",
                                             f"the cited turn mentions {names}, not {_MONTH_NAMES[month - 1].capitalize()}",
                                             lead))


def check_numbers(answer: str, meetings: list[Meeting], report: CheckReport) -> list[NumberFinding]:
    """Look up every number and date in the answer in the turn it is cited to.

    The text in front of a citation (back to the previous one) is what that citation supports. Numbers in a stretch
    of text with no citation are only looked up in the transcripts as a whole. Small counts written as words ("three
    days") are not checked, and neither are times like 04:09 or the numbers in citation tags and meeting titles."""
    if answer.strip().lower().startswith("not found in the transcripts"):
        return []
    matches = list(CITE_RE.finditer(answer))
    if len(matches) != len(report.citations):
        return []
    groups: list[list[int]] = []
    for i, m in enumerate(matches):
        if groups and _GAP.fullmatch(answer[matches[groups[-1][-1]].end(): m.start()]):
            groups[-1].append(i)
        else:
            groups.append([i])

    findings: list[NumberFinding] = []
    pos = 0
    for line in answer.split("\n"):
        line_start, line_end = pos, pos + len(line)
        pos = line_end + 1
        cursor = line_start
        for g in groups:
            start, end = matches[g[0]].start(), matches[g[-1]].end()
            if start < line_start or end > line_end:
                continue
            _check_unit(answer[cursor:start], [report.citations[i] for i in g], meetings, findings)
            cursor = end
        _check_unit(answer[cursor:line_end], [], meetings, findings)
    return findings


def describe_numbers(findings: list[NumberFinding]) -> list[str]:
    """One line per distinct problem, for the message under an answer."""
    lines, seen = [], set()
    for f in findings:
        if f.status == "ok" or (f.value, f.status) in seen:
            continue
        seen.add((f.value, f.status))
        if f.kind == "date":
            lines.append(f"{f.value}: {f.where}")
        elif f.status == "elsewhere":
            lines.append(f"{f.value}: not in the cited turn (it appears in {f.where})")
        else:
            lines.append(f"{f.value}: not found in the transcripts (it may be a calculation)")
    return lines
