"""OPS-01 statistics: p50/p95/p99 latency, failure rate and cost per successful task, from the per-attempt rows that
ops_eval.py writes. Pure functions, no network, so the scorecard builder and the tests use them too.

Definitions (see OPS-01.md):
  call success      a response came back, it ended normally, and there was no error
  task success      call success AND every check of the question passed (the same checks as run_eval.py)
  failure           an attempt that is not a call success, unless the person could fix it themselves (a bad key, no credit,
                    a spending limit): those are reported but kept out of the failure rate
  cost per success  the cost of ALL attempts, failed ones included, divided by the number of task successes
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from meetminer.engine import USER_FIXABLE  # noqa: E402
from meetminer.reports import percentile  # noqa: E402


def load_rows(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def wilson_upper(failures: int, n: int, z: float = 1.96) -> float | None:
    """The upper end of the 95% confidence interval for a failure rate. With no failures in n attempts it is about 3/n."""
    if n <= 0:
        return None
    p = failures / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return min(1.0, (centre + margin) / (1 + z * z / n))


@dataclass
class GroupStats:
    model: str
    cell: str
    cache_state: str  # "cold" | "warm" | "all"
    attempts: int
    user_fixable: int
    counted: int  # attempts that count toward the failure rate
    failures: int
    failure_rate: float | None
    failure_upper95: float | None
    p50: float | None
    p95: float | None
    p99: float | None
    task_successes: int
    task_success_rate: float | None
    cost_total: float
    cost_per_success: float | None
    corpus_tokens: int
    citations_ok: int
    citations_weak: int
    citations_bad: int
    answers_with_bad_citation: int
    answers_with_flagged_numbers: int
    answered: int
    truncated: int
    failure_kinds: dict


def _stats(model: str, cell: str, state: str, rows: list[dict]) -> GroupStats:
    fixable = [r for r in rows if not r["ok"] and r.get("failure_kind") in USER_FIXABLE]
    counted = [r for r in rows if r not in fixable]
    failures = [r for r in counted if not r["ok"]]
    answered = [r for r in rows if r["ok"]]
    latencies = [r["latency_s"] for r in answered]
    wins = [r for r in rows if r.get("task_success")]
    cost = sum(r.get("cost_usd", 0.0) for r in rows)
    kinds: dict[str, int] = {}
    for r in failures:
        kinds[r.get("failure_kind") or "unknown"] = kinds.get(r.get("failure_kind") or "unknown", 0) + 1
    cites = {k: sum(r.get("citations", {}).get(k, 0) for r in answered) for k in ("ok", "weak", "bad")}
    return GroupStats(
        model=model, cell=cell, cache_state=state, attempts=len(rows), user_fixable=len(fixable), counted=len(counted),
        failures=len(failures), failure_rate=len(failures) / len(counted) if counted else None,
        failure_upper95=wilson_upper(len(failures), len(counted)),
        p50=percentile(latencies, 50), p95=percentile(latencies, 95), p99=percentile(latencies, 99),
        task_successes=len(wins), task_success_rate=len(wins) / len(rows) if rows else None,
        cost_total=cost, cost_per_success=cost / len(wins) if wins else None,
        corpus_tokens=max((r.get("corpus_tokens", 0) for r in rows), default=0),
        citations_ok=cites["ok"], citations_weak=cites["weak"], citations_bad=cites["bad"],
        answers_with_bad_citation=sum(1 for r in answered if r.get("citations", {}).get("bad", 0)
                                      or r.get("uncited_missing", 0)),
        answers_with_flagged_numbers=sum(1 for r in answered if r.get("numbers", {}).get("flagged", 0)),
        answered=len(answered), truncated=sum(1 for r in answered if r.get("stop_reason") == "max_tokens"),
        failure_kinds=kinds)


def summarize(rows: list[dict]) -> list[GroupStats]:
    """One GroupStats per (model, cell) for cold attempts, warm attempts, and all of them together."""
    out: list[GroupStats] = []
    for model, cell in sorted({(r["model"], r["cell"]) for r in rows}):
        group = [r for r in rows if r["model"] == model and r["cell"] == cell]
        for state in ("cold", "warm", "all"):
            part = group if state == "all" else [r for r in group if r["cache_state"] == state]
            if part:
                out.append(_stats(model, cell, state, part))
    return out


def find(stats: list[GroupStats], model: str, cell: str, state: str) -> GroupStats | None:
    return next((s for s in stats if (s.model, s.cell, s.cache_state) == (model, cell, state)), None)


def fmt_pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def fmt_s(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f} s"


def fmt_usd(value: float | None) -> str:
    return "n/a" if value is None else (f"${value:.5f}" if value < 0.1 else f"${value:.3f}")


def report(stats: list[GroupStats]) -> str:
    """A plain-text table of every group."""
    lines = [f"{'model':<20}{'cell':<5}{'cache':<6}{'n':>5}{'p50':>9}{'p95':>9}{'p99':>9}{'fail':>8}{'upper95':>9}"
             f"{'tasks ok':>10}{'cost/success':>14}"]
    for s in stats:
        lines.append(f"{s.model:<20}{s.cell:<5}{s.cache_state:<6}{s.attempts:>5}{fmt_s(s.p50):>9}{fmt_s(s.p95):>9}"
                     f"{fmt_s(s.p99):>9}{fmt_pct(s.failure_rate):>8}{fmt_pct(s.failure_upper95):>9}"
                     f"{fmt_pct(s.task_success_rate, 0):>10}{fmt_usd(s.cost_per_success):>14}")
    return "\n".join(lines)
