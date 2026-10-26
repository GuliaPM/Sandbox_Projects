"""The usage log (usage.csv): one line per attempt to answer a question, successful or not.

Older versions of the app wrote no `status`, `error_kind` or `engine` columns and logged only answers that came back.
Such a file is upgraded in place the first time a line is added, and `read_rows` reads every layout (a line without an
engine was answered with full context, the only engine those versions had).
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

COLUMNS = ["time", "model", "effort", "input", "cache_write", "cache_read", "output", "cost_usd", "seconds",
           "question", "status", "error_kind", "engine"]
NUMERIC = ("input", "cache_write", "cache_read", "output")
DEFAULTS = {"status": "ok", "engine": "full"}  # what an older log without these columns meant


def read_rows(path: Path) -> list[dict]:
    """Every readable line of the log as a dict with numbers converted. Damaged lines are skipped, never raised."""
    rows: list[dict] = []
    try:
        with path.open(newline="", encoding="utf-8-sig") as f:
            for raw in csv.DictReader(f):
                try:
                    when = datetime.fromisoformat((raw.get("time") or "").strip())
                    row = {
                        "time": when, "model": raw.get("model") or "", "effort": raw.get("effort") or "",
                        "cost_usd": float(raw.get("cost_usd") or 0.0), "seconds": float(raw.get("seconds") or 0.0),
                        "question": raw.get("question") or "",
                        "status": (raw.get("status") or "ok").strip() or "ok",
                        "error_kind": (raw.get("error_kind") or "").strip(),
                        "engine": (raw.get("engine") or "full").strip() or "full",
                    }
                    for name in NUMERIC:
                        row[name] = int(float(raw.get(name) or 0))
                except (ValueError, TypeError):
                    continue
                rows.append(row)
    except OSError:
        return []
    return rows


def _upgrade(path: Path) -> None:
    """Rewrite a log that has the old column layout so new lines line up with its header."""
    try:
        with path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header is None or header == COLUMNS:
                return
            old = [dict(zip(header, line)) for line in reader]
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(COLUMNS)
            for r in old:
                writer.writerow([r.get(c) or DEFAULTS.get(c, "") for c in COLUMNS])
        tmp.replace(path)
    except OSError:
        pass


def append(path: Path, row: dict) -> None:
    """Add one line. A locked or read-only log must never break answering, so errors are swallowed."""
    try:
        if path.exists():
            _upgrade(path)
        new_file = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            if new_file:
                writer.writerow(COLUMNS)
            writer.writerow([row.get(c, "") for c in COLUMNS])
    except OSError:
        pass
