"""The numbers behind the Reports page: your own usage, read from usage.csv, and the release scorecard.

The scorecard is a JSON file written by scorecards/build_scorecard.py in the repository. This module only reads it.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import storage

SCORECARD_FILE = "scorecard.json"
RATINGS = ("green", "amber", "red", "unmeasured", "info")


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile: the smallest value that at least q percent of the values are at or below."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q / 100 * len(ordered)) - 1)]


# --------------------------------------------------------------------------- your usage


@dataclass
class ModelStats:
    model: str
    answers: int
    p50: float | None
    p95: float | None
    average_cost: float
    total_cost: float


@dataclass
class UsageSummary:
    days: int
    answers: int = 0
    failed: int = 0  # attempts that gave no usable answer, whatever the reason
    system_failed: int = 0  # the ones that are not something you can fix yourself (timeouts, outages...)
    p50: float | None = None
    p95: float | None = None
    average_cost: float = 0.0
    total_cost: float = 0.0
    by_model: list[ModelStats] = field(default_factory=list)

    @property
    def attempts(self) -> int:
        return self.answers + self.failed

    @property
    def failure_rate(self) -> float | None:
        """Share of attempts that failed for a reason outside your control. None until there is something to measure."""
        counted = self.answers + self.system_failed
        return self.system_failed / counted if counted else None


def summarize_usage(rows: list[dict], now: datetime | None = None, days: int = 30) -> UsageSummary:
    from .engine import USER_FIXABLE  # engine does not import this module, so this is only a late import for tidiness

    now = now or datetime.now()
    since = now - timedelta(days=days)
    out = UsageSummary(days=days)
    seconds: list[float] = []
    per_model: dict[str, list[dict]] = {}
    for row in rows:
        if row["time"] < since:
            continue
        if row["status"] == "ok":
            out.answers += 1
            out.total_cost += row["cost_usd"]
            seconds.append(row["seconds"])
            per_model.setdefault(row["model"], []).append(row)
        else:
            out.failed += 1
            if row["error_kind"] not in USER_FIXABLE:
                out.system_failed += 1
            out.total_cost += row["cost_usd"]
    out.p50, out.p95 = percentile(seconds, 50), percentile(seconds, 95)
    out.average_cost = out.total_cost / out.answers if out.answers else 0.0
    for model, items in sorted(per_model.items(), key=lambda kv: -len(kv[1])):
        times = [r["seconds"] for r in items]
        cost = sum(r["cost_usd"] for r in items)
        out.by_model.append(ModelStats(model, len(items), percentile(times, 50), percentile(times, 95),
                                       cost / len(items), cost))
    return out


# --------------------------------------------------------------------------- the release scorecard


class ScorecardError(Exception):
    """The file is not a scorecard this version of the app can show; the message says why."""


def _checkout_scorecard() -> Path:
    root = storage._checkout_root()
    return (root / "scorecards" / "latest.json") if root else Path()


def scorecard_candidates() -> list[Path]:
    """Where to look, in order: one you saved for yourself, the repository's latest one (from a source checkout),
    and the one that was packaged with this release."""
    return [storage.data_dir() / "reports" / SCORECARD_FILE, _checkout_scorecard(),
            storage.resource_dir() / "reports" / "release-scorecard.json"]


def source_label(path: Path) -> str:
    """Where a scorecard came from, in words, without the long path (which names the Windows user)."""
    def inside(folder: Path) -> bool:
        try:
            path.resolve().relative_to(folder.resolve())
            return True
        except (ValueError, OSError):
            return False

    checkout = storage._checkout_root()
    if inside(storage.data_dir() / "reports"):
        return f"a copy in your data folder (reports\\{path.name})"
    if checkout and inside(checkout / "scorecards"):
        return f"scorecards\\{path.name} in the repository"
    if inside(storage.resource_dir() / "reports"):
        return "packaged with this release"
    return str(path)


def load_scorecard(path: Path) -> dict:
    try:
        card = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ScorecardError(f"Could not read {path.name}: {exc}") from exc
    if not isinstance(card, dict) or card.get("schema") != 1 or not isinstance(card.get("dimensions"), list):
        raise ScorecardError(f"{path.name} is not a MeetMiner scorecard (expected schema 1).")
    return card


def find_scorecard(extra: Path | None = None) -> tuple[Path, dict] | None:
    for path in ([extra] if extra else []) + scorecard_candidates():
        if path and path.is_file():
            try:
                return path, load_scorecard(path)
            except ScorecardError:
                continue
    return None


def headline(card: dict) -> str:
    when = str(card.get("generated", ""))[:10]
    total = card.get("total")
    score = f"{total:.0f}/100" if isinstance(total, (int, float)) else "no score"
    return f"{card.get('app', 'MeetMiner')} {card.get('version', '')} · {card.get('decision', '?')} · {score}" + \
           (f" · measured {when}" if when else "")


def metric_rows(card: dict) -> list[tuple[str, dict]]:
    """[(dimension name, metric)] in display order."""
    return [(d.get("name", d.get("id", "")), m) for d in card["dimensions"] for m in d.get("metrics", [])]
