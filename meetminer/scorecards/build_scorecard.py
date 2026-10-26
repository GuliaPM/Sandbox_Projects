"""Build the MeetMiner release scorecard from evidence and write scorecards/latest.json and latest.md.

    python scorecards/build_scorecard.py                  gather everything and score it (takes a few minutes)
    python scorecards/build_scorecard.py --engine rag     the same for the RAG engine (latest-rag.json, latest-rag.md)
    python scorecards/build_scorecard.py --skip-sandbox   leave out the install test (it opens windows)
    python scorecards/build_scorecard.py --skip-tests --skip-scan --skip-sandbox    only score the eval results

Evidence, newest file wins:
  evals/results/ops-<model>-S-*.jsonl   from evals/ops_eval.py   (also -M- and -L- for the information rows)
  evals/results/quality-<model>-*.json  from evals/run_eval.py
  evals/results/probes-<model>-*.json   from evals/run_probes.py
  evals/results/retrieval-*.json        from evals/retrieval_eval.py (RAG; chosen by the question set and filler recorded
                                        in the file, so held-out and hard-distractor runs never replace the default one)
  evals/results/quality-heldout-<model>-*.json and the held-out RET-01 runs, for the held-out information rows
  the unit tests (run here), the release scan (packaging/export_public.py), the install test (packaging/sandbox_test.ps1)
What is measured, and what counts as good, is in scorecard.spec.yaml. A metric with no evidence is "not measured" and scores 0.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "evals"))

import opsstats  # noqa: E402
from meetminer.version import APP_NAME, __version__  # noqa: E402

RESULTS = ROOT / "evals" / "results"
SPECS = {"full": HERE / "scorecard.spec.yaml", "rag": HERE / "scorecard-rag.spec.yaml"}
SPEC = SPECS["full"]
ENGINE_NAMES = {"full": "full context", "rag": "RAG"}
CELLS = ("S", "M", "L", "XL")


def kind(prefix: str, engine: str) -> str:
    """Result files of the RAG engine carry -rag after their kind; full context keeps the original names."""
    return f"{prefix}-rag" if engine == "rag" else prefix
RATING_TEXT = {"green": "good", "amber": "watch", "red": "fix", "unmeasured": "not measured", "info": "info"}


# --------------------------------------------------------------------------- evidence


@dataclass
class Evidence:
    model: str
    cell: str
    ops: list = field(default_factory=list)  # opsstats.GroupStats for every file used
    ops_files: list = field(default_factory=list)
    quality: dict | None = None
    quality_file: str = ""
    probes: dict | None = None
    probes_file: str = ""
    tests: dict | None = None  # test id -> "ok" | "fail" | "skipped"
    scan: tuple | None = None  # (clean, detail)
    sandbox: tuple | None = None
    docs: tuple | None = None
    other_notes: list = field(default_factory=list)  # what the evals found for the models that are not the default one
    engine: str = "full"
    retrieval: dict | None = None  # RAG: evals/retrieval_eval.py results
    retrieval_file: str = ""
    # The frozen held-out set (evals/heldout/questions.yaml): information only, never a gate yet.
    heldout_quality: dict | None = None
    heldout_quality_file: str = ""
    heldout_retrieval: dict = field(default_factory=dict)  # distractors ("easy" | "hard") -> retrieval_eval.py results
    heldout_retrieval_files: list = field(default_factory=list)


def newest(pattern: str) -> Path | None:
    files = sorted(RESULTS.glob(pattern), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def newest_retrieval(questions_set: str = "original", distractors: str = "easy") -> Path | None:
    """The newest RET-01 result for this question set and filler, with the current synonym list. Files written before
    these fields existed count as the original questions, easy filler, and the synonyms of their time."""
    for path in sorted(RESULTS.glob("retrieval-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        data = json.loads(path.read_text(encoding="utf-8"))
        if (data.get("questions_set", "original"), data.get("distractors", "easy"),
                data.get("synonyms", "current")) == (questions_set, distractors, "current"):
            return path
    return None


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def run_unit_tests() -> dict:
    """Run tests/ in this process and return {test id: outcome}."""
    class Collect(unittest.TextTestResult):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.outcomes: dict[str, str] = {}

        def addSuccess(self, test):
            super().addSuccess(test)
            self.outcomes.setdefault(test.id(), "ok")

        def addFailure(self, test, err):
            super().addFailure(test, err)
            self.outcomes[test.id()] = "fail"

        def addError(self, test, err):
            super().addError(test, err)
            self.outcomes[test.id()] = "fail"

        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            if err is not None:
                self.outcomes[test.id()] = "fail"

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            self.outcomes[test.id()] = "skipped"

    tests_dir = ROOT / "tests"
    sys.path.insert(0, str(tests_dir))
    suite = unittest.defaultTestLoader.discover(str(tests_dir), top_level_dir=str(tests_dir))
    stream = open(tempfile.gettempdir() + "/meetminer-scorecard-tests.log", "w", encoding="utf-8")
    result = unittest.TextTestRunner(stream=stream, resultclass=Collect, verbosity=1).run(suite)
    stream.close()
    return result.outcomes


def run_scan() -> tuple:
    """The release scan, as part of exporting the publishable copy of the repository."""
    with tempfile.TemporaryDirectory() as tmp:
        done = subprocess.run([sys.executable, str(ROOT / "packaging" / "export_public.py"), "--out", tmp],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT)
    lines = [ln for ln in (done.stdout or "").strip().splitlines() if ln.strip()]
    clean = done.returncode == 0 and any(ln.startswith("CLEAN") for ln in lines)
    return clean, next((ln for ln in lines if ln.startswith(("CLEAN", "FOUND"))), (lines[-1] if lines else "no output"))


def run_sandbox() -> tuple | None:
    script = ROOT / "packaging" / "sandbox_test.ps1"
    if not script.is_file() or not list((ROOT / "dist").glob("*-Setup-*.exe")):
        return None
    done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT, timeout=900)
    line = next((ln for ln in (done.stdout or "").splitlines() if ln.startswith("SANDBOX RESULT")), "no result line")
    return "PASS" in line, line


def check_docs() -> tuple:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    unbounded = [d for d in deps if d.lower().startswith("anthropic") and "<" not in d]
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    has_entry = f"## [{__version__}]" in log
    problems = ([f"no upper bound on {d}" for d in unbounded] + ([] if has_entry else [f"CHANGELOG has no entry for {__version__}"]))
    return not problems, "; ".join(problems) or "anthropic is bounded and the changelog has this version"


def gather(spec: dict, args) -> Evidence:
    model = args.model or spec["default_model"]
    engine = getattr(args, "engine", "full")
    ev = Evidence(model=model, cell=spec.get("ops_cell", "S"), engine=engine)
    ops_paths = [Path(p) for p in args.ops] if args.ops else [
        p for p in (newest(f"{kind('ops', engine)}-{m}-{c}-*.jsonl") for m in sorted({model, *spec_models()})
                    for c in CELLS) if p]
    for path in ops_paths:
        ev.ops += opsstats.summarize(opsstats.load_rows([path]))
        ev.ops_files.append(rel(path))
    q = Path(args.quality) if args.quality else newest(f"{kind('quality', engine)}-{model}-*.json")
    if q and q.is_file():
        ev.quality, ev.quality_file = json.loads(q.read_text(encoding="utf-8")), rel(q)
    p = Path(args.probes) if args.probes else newest(f"{kind('probes', engine)}-{model}-*.json")
    if p and p.is_file():
        ev.probes, ev.probes_file = json.loads(p.read_text(encoding="utf-8")), rel(p)
    r = newest_retrieval() if engine == "rag" else None
    if r:
        ev.retrieval, ev.retrieval_file = json.loads(r.read_text(encoding="utf-8")), rel(r)
    hq = newest(f"{kind('quality', engine)}-heldout-{model}-*.json")
    if hq:
        ev.heldout_quality, ev.heldout_quality_file = json.loads(hq.read_text(encoding="utf-8")), rel(hq)
    for distractors in ("easy", "hard"):
        hr = newest_retrieval("heldout", distractors)
        if hr:
            ev.heldout_retrieval[distractors] = json.loads(hr.read_text(encoding="utf-8"))
            ev.heldout_retrieval_files.append(rel(hr))
    if not args.skip_tests:
        print("running the unit tests...", flush=True)
        ev.tests = run_unit_tests()
    if not args.skip_scan:
        print("running the release scan...", flush=True)
        ev.scan = run_scan()
    if not args.skip_sandbox:
        print("running the install test...", flush=True)
        ev.sandbox = run_sandbox()
    ev.docs = check_docs()
    ev.other_notes = other_model_findings([m for m in spec_models() if m != model], engine)
    return ev


def spec_models() -> list[str]:
    return ["claude-sonnet-5-5"]  # models whose results are shown as information next to the default one


def other_model_findings(models: list[str], engine: str = "full") -> list[str]:
    """One note per other model: what its quality eval and injection probes found (information, not scored)."""
    notes = []
    for model in models:
        q = newest(f"{kind('quality', engine)}-{model}-*.json")
        if q:
            data = json.loads(q.read_text(encoding="utf-8"))
            cites = [c for o in data["outcomes"] for c in o["citations"]]
            failed = [f"{o['id']} ({', '.join(o['failed_checks'])})" for o in data["outcomes"] if not o["passed"]]
            notes.append(f"{model}, quality eval: {data['passed']}/{data['questions']} questions passed; citations "
                         f"{cites.count('ok')} ok, {cites.count('weak')} weak, {cites.count('bad')} bad"
                         + (f"; failed: {'; '.join(failed)}" if failed else "") + f" [{rel(q)}]")
        p = newest(f"{kind('probes', engine)}-{model}-*.json")
        if p:
            data = json.loads(p.read_text(encoding="utf-8"))
            notes.append(f"{model}, injection probes: {data['resisted']}/{data['probes']} resisted [{rel(p)}]")
    return notes


# --------------------------------------------------------------------------- measuring


@dataclass
class Measure:
    value: float
    shown: str
    note: str = ""


def _ratio(num: int, den: int, label: str = "") -> Measure | None:
    return Measure(num / den, f"{num}/{den} ({num / den * 100:.1f}%)" + label) if den else None


def _group(ev: Evidence, cell: str, state: str, model: str | None = None):
    return opsstats.find(ev.ops, model or ev.model, cell, state, ev.engine)


def _tests_passing(ev: Evidence, names: list[str]) -> Measure | None:
    if ev.tests is None:
        return None
    found = {n: next((v for k, v in ev.tests.items() if k.endswith("." + n)), None) for n in names}
    missing = [n for n, v in found.items() if v is None]
    failing = [n for n, v in found.items() if v == "fail"]
    ok = not missing and not failing
    note = "" if ok else ("missing: " + ", ".join(missing) if missing else "failing: " + ", ".join(failing))
    return Measure(1.0 if ok else 0.0, "yes" if ok else "no", note)


KEY_TESTS = ["test_key_is_encrypted_on_disk_and_round_trips", "test_key_hint_never_shows_the_whole_key"]
LIMIT_TESTS = ["test_daily_limit_reached", "test_monthly_limit_reached_even_when_today_is_fine",
               "test_the_limit_blocks_the_call_and_survives_a_restart", "test_spending_limit_is_saved_and_enforced",
               "test_a_daily_limit_that_is_reached_pauses_questions_and_says_so"]
RAG_TESTS = ["test_no_match_answers_not_found_without_calling_the_api", "test_a_rag_question_that_matches_nothing_costs_nothing",
             "test_the_index_never_touches_the_disk", "test_rag_sends_only_the_excerpts_and_no_cache_breakpoint"]


def measure(source: str, ev: Evidence) -> Measure | None:
    """The value of one metric, or None when there is no evidence for it."""
    kind, _, what = source.partition(".")
    q_out = ev.quality["outcomes"] if ev.quality else []
    if kind == "quality" and ev.quality:
        pick = {"correct": lambda o: o.get("kind") in ("lookup", "commitment"),
                "traps": lambda o: o.get("kind") == "not_in_data", "comparison": lambda o: o.get("kind") == "comparison"}[what]
        items = [o for o in q_out if pick(o)]
        return _ratio(sum(o["passed"] for o in items), len(items))
    if kind in ("citations", "numbers"):
        ops_all = _group(ev, ev.cell, "all")
        answered = len(q_out) + (ops_all.answered if ops_all else 0)
        if not answered:
            return None
        ok = sum(o["citations"].count("ok") for o in q_out) + (ops_all.citations_ok if ops_all else 0)
        weak = sum(o["citations"].count("weak") for o in q_out) + (ops_all.citations_weak if ops_all else 0)
        bad = sum(o["citations"].count("bad") for o in q_out) + (ops_all.citations_bad if ops_all else 0)
        total = ok + weak + bad
        if what == "verified":
            return _ratio(ok, total, f" of {answered} answers")
        if what == "weak":
            return _ratio(weak, total, " of citations") if total else None
        if what == "bad_answers":
            bad_answers = sum(1 for o in q_out if "bad" in o["citations"] or o.get("uncited_missing")) + \
                (ops_all.answers_with_bad_citation if ops_all else 0)
            return _ratio(bad_answers, answered)
        if what == "flagged_answers":
            flagged = sum(1 for o in q_out if o.get("numbers", {}).get("flagged")) + \
                (ops_all.answers_with_flagged_numbers if ops_all else 0)
            return _ratio(flagged, answered)
    if kind == "ops":
        cell = ev.cell
        name = what
        for size in ("_XL", "_M", "_L"):
            if name.endswith(size):
                name, cell = name[: -len(size)], size[1:]
                break
        state = {"p95_cold": "cold", "p95_warm": "warm"}.get(name, "all")  # RAG's plain "p95" covers every attempt
        g = _group(ev, cell, state)
        if g is None:
            return None
        if name in ("p95_warm", "p95_cold", "p95"):
            return Measure(g.p95, f"{g.p95:.2f} s", f"n={g.attempts}") if g.p95 is not None else None
        if name == "failure_rate":
            if g.failure_rate is None:
                return None
            return Measure(g.failure_rate, f"{g.failure_rate * 100:.1f}% of {g.counted} (95% upper bound "
                           f"{g.failure_upper95 * 100:.1f}%)", ", ".join(f"{k}: {v}" for k, v in g.failure_kinds.items()))
        if name == "task_success":
            return _ratio(g.task_successes, g.attempts)
        if name == "cost_per_success":
            return Measure(g.cost_per_success, opsstats.fmt_usd(g.cost_per_success),
                           f"${g.cost_total:.4f} over {g.attempts} attempts") if g.cost_per_success is not None else None
    if kind == "retrieval" and ev.retrieval:
        metric, _, cell = what.rpartition("_")
        r = ev.retrieval["cells"].get(cell)
        if r is None:
            return None
        if metric == "found":
            return _ratio(r["all_answer_turns_found"], r["questions"], f" ({r['meetings']} meetings)")
        if metric == "share":
            share = r["share_of_full_context"]
            return Measure(share, f"{share * 100:.0f}% (about {r['avg_tokens_sent_est']:,} of "
                                  f"{r['full_context_tokens_est']:,} tokens)", ev.retrieval_file)
        if metric == "traps":
            return _ratio(r["traps_without_a_call"], r["traps"], " ended without a call")
    if kind == "heldout":
        if what == "quality" and ev.heldout_quality:
            out = ev.heldout_quality["outcomes"]
            m = _ratio(sum(o["passed"] for o in out), len(out))
            return Measure(m.value, m.shown, ev.heldout_quality_file) if m else None
        if what.startswith("found_"):  # found_S, found_XL_hard
            cell, _, distractors = what[len("found_"):].partition("_")
            r = ev.heldout_retrieval.get(distractors or "easy", {}).get("cells", {}).get(cell)
            if r is None:
                return None
            label = f" ({r['meetings']} meetings{', hard distractors' if distractors == 'hard' else ''})"
            return _ratio(r["all_answer_turns_found"], r["questions"], label)
    if kind == "probes" and ev.probes:
        misses = ev.probes["probes"] - ev.probes["resisted"]
        return Measure(misses, f"{ev.probes['resisted']}/{ev.probes['probes']} resisted", ev.probes_file)
    if kind == "scan" and ev.scan:
        return Measure(1.0 if ev.scan[0] else 0.0, "clean" if ev.scan[0] else "problems found", ev.scan[1])
    if kind == "sandbox" and ev.sandbox:
        return Measure(1.0 if ev.sandbox[0] else 0.0, "pass" if ev.sandbox[0] else "fail", ev.sandbox[1])
    if kind == "release" and ev.docs:
        return Measure(1.0 if ev.docs[0] else 0.0, "yes" if ev.docs[0] else "no", ev.docs[1])
    if kind == "tests" and ev.tests is not None:
        if what == "key_encrypted":
            return _tests_passing(ev, KEY_TESTS)
        if what == "spend_limits":
            return _tests_passing(ev, LIMIT_TESTS)
        if what == "rag_no_match":
            return _tests_passing(ev, RAG_TESTS)
        scope = {k: v for k, v in ev.tests.items() if what == "all" or k.startswith("test_guardrails.")}
        ran = {k: v for k, v in scope.items() if v != "skipped"}
        return _ratio(sum(v == "ok" for v in ran.values()), len(ran), (f", {len(scope) - len(ran)} skipped" if len(scope) != len(ran) else ""))
    return None


# --------------------------------------------------------------------------- scoring


def fmt(value: float, how: str) -> str:
    if how in ("percent", "fraction"):
        return f"{value * 100:g}%"
    return {"seconds": f"{value:g} s", "usd": f"${value:g}", "yesno": "yes", "count": f"{value:g}"}.get(how, f"{value:g}")


def thresholds(m: dict, model: str) -> tuple[float | None, float | None]:
    if "green_by_model" in m:
        green = m["green_by_model"].get(model)
        return green, (green * m.get("amber_factor", 2) if green is not None else None)
    return m.get("green"), m.get("amber")


def rate(m: dict, value: float | None, model: str) -> str:
    if m.get("info"):
        return "info"
    green, amber = thresholds(m, model)
    if value is None or green is None:
        return "unmeasured"
    if m["higher_is_better"]:
        return "green" if value >= green else "amber" if value >= amber else "red"
    return "green" if value <= green else "amber" if value <= amber else "red"


def target_text(m: dict, model: str) -> str:
    if m.get("info"):
        return "for information"
    green, amber = thresholds(m, model)
    if green is None:
        return "no target for this model"
    how = m.get("format", "")
    if how == "yesno":
        return "yes"
    if m["higher_is_better"]:
        return f"≥ {fmt(green, how)}" + (f" (amber ≥ {fmt(amber, how)})" if amber != green else "")
    return f"≤ {fmt(green, how)}" + (f" (amber ≤ {fmt(amber, how)})" if amber != green else "")


def build(spec: dict, ev: Evidence) -> dict:
    points = spec["scoring"]["points"]
    dimensions, gates_failed, gates_open, unmeasured = [], [], [], []
    for dim in spec["dimensions"]:
        rows, earned, possible = [], 0, 0
        for m in dim["metrics"]:
            got = measure(m["source"], ev)
            rating = rate(m, got.value if got else None, ev.model)
            if rating != "info":
                possible += max(points.values())
                earned += points.get(rating, 0)
            if rating == "unmeasured":
                unmeasured.append(m["name"])
            if m.get("gate"):
                if rating == "red":
                    gates_failed.append(m["name"])
                elif rating == "unmeasured":
                    gates_open.append(m["name"])
            rows.append({"id": m["id"], "name": m["name"], "target": target_text(m, ev.model),
                         "measured": got.shown if got else "not measured", "rating": rating,
                         "gate": bool(m.get("gate")), "note": got.note if got else ""})
        dimensions.append({"id": dim["id"], "name": dim["name"], "weight": dim["weight"],
                           "score": round(earned / possible * 100, 1) if possible else None, "metrics": rows})
    weights = sum(d["weight"] for d in dimensions if d["score"] is not None)
    total = round(sum(d["weight"] * d["score"] for d in dimensions if d["score"] is not None) / weights, 1) if weights else 0.0
    sc = spec["scoring"]
    if gates_failed or gates_open or total < sc["review_min"]:
        decision = "HOLD"
    elif total >= sc["ship_min"] and not unmeasured:
        decision = "SHIP"
    else:
        decision = "REVIEW"
    notes = ["The operational targets are proposed starting points (evals/OPS-01.md); the numbers are measured, the targets are choices."]
    notes += list(spec.get("notes", []))
    notes += other_model_notes(ev) + ev.other_notes
    return {
        "schema": 1, "app": APP_NAME, "version": __version__, "engine": ev.engine,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "decision": decision, "total": total, "gates_failed": gates_failed, "gates_unmeasured": gates_open,
        "unmeasured": unmeasured, "config": {"model": ev.model, "engine": ev.engine, "cell": ev.cell,
                                             "transcripts": "fictional samples (samples/transcripts)"},
        "dimensions": dimensions, "notes": notes, "guardrails": spec.get("guardrails", []),
        "evidence": {"ops": ev.ops_files, "quality": ev.quality_file, "probes": ev.probes_file,
                     "retrieval": ev.retrieval_file or None,
                     "heldout": [f for f in [ev.heldout_quality_file, *ev.heldout_retrieval_files] if f] or None,
                     "unit_tests": None if ev.tests is None else f"{sum(v == 'ok' for v in ev.tests.values())}/{len(ev.tests)} ok",
                     "scan": ev.scan[1] if ev.scan else None, "sandbox": ev.sandbox[1] if ev.sandbox else None},
    }


def other_model_notes(ev: Evidence) -> list[str]:
    notes = []
    for g in ev.ops:
        if g.cache_state != "all" or g.model == ev.model or g.engine != ev.engine:  # the table has the default model's
            continue
        notes.append(f"{g.model}, cell {g.cell} ({g.corpus_tokens:,} tokens, {g.attempts} attempts): p95 {opsstats.fmt_s(g.p95)}, "
                     f"failure rate {opsstats.fmt_pct(g.failure_rate)}, {opsstats.fmt_usd(g.cost_per_success)} per successful task.")
    return notes


LIMITS = [
    "The 10 original questions (evals/questions.yaml) are a regression baseline on a development set: the retriever was "
    "built while looking at them, so a good score on them shows that nothing regressed, not that the app generalises.",
    "The held-out rows (evals/heldout/questions.yaml) ask paraphrased questions that nothing was tuned on. That set was "
    "written by a model (Claude Code), not by a person, and is about the same two fictional calls, so a human-written "
    "held-out set on real transcripts is still the open next step. Its rows are information, not gates.",
    "n is small: 10 original and 12 held-out questions, two short fictional calls (the larger sizes add synthetic filler "
    "meetings), and 30 to 100 attempts per operational row, so one question moves a percentage by 8 to 12 points.",
]


def markdown(card: dict) -> str:
    engine = ENGINE_NAMES.get(card.get("engine", "full"), "full context")
    lines = [f"# {card['app']} {card['version']} release scorecard: {engine} engine", "",
             f"**Decision: {card['decision']}**  ·  score {card['total']:.0f}/100  ·  measured {card['generated'][:10]}  ·  "
             f"configuration: {card['config']['model']}, {engine}, cell {card['config']['cell']} "
             f"({card['config']['transcripts']})", ""]
    if card["gates_failed"]:
        lines += ["Blocked by: " + "; ".join(card["gates_failed"]), ""]
    if card["gates_unmeasured"]:
        lines += ["Gates not measured yet: " + "; ".join(card["gates_unmeasured"]), ""]
    for d in card["dimensions"]:
        score = "n/a" if d["score"] is None else f"{d['score']:.0f}/100"
        lines += [f"## {d['name']}  ({score}, weight {d['weight']}%)", "", "| Check | Target | Measured | Result |", "|---|---|---|---|"]
        for m in d["metrics"]:
            lines.append(f"| {m['name']}{' **[gate]**' if m['gate'] else ''} | {m['target']} | {m['measured']} | {RATING_TEXT[m['rating']]} |")
        lines.append("")
    lines += ["## What this scorecard does and does not show", "", *[f"- {s}" for s in LIMITS], ""]
    lines += ["## Evidence", ""]
    ev = card["evidence"]
    lines += [f"- Operational runs: {', '.join(ev['ops']) or 'none'}", f"- Quality eval: {ev['quality'] or 'none'}"]
    if ev.get("retrieval"):
        lines.append(f"- Retrieval eval: {ev['retrieval']}")
    if ev.get("heldout"):
        lines.append(f"- Held-out set (information only): {', '.join(ev['heldout'])}")
    lines += [
              f"- Injection probes: {ev['probes'] or 'none'}", f"- Unit tests: {ev['unit_tests'] or 'not run'}",
              f"- Release scan: {ev['scan'] or 'not run'}", f"- Install test: {ev['sandbox'] or 'not run'}", ""]
    lines += ["## Notes", ""] + [f"- {n}" for n in card["notes"]]
    lines += ["", "Guardrails exercised: " + ", ".join(card["guardrails"]), "",
              f"Sign-off: ______________________   Date: ____________   Decision: SHIP / SHIP WITH WAIVERS / HOLD", ""]
    return "\n".join(lines)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", choices=list(SPECS), default="full",
                        help="which engine to score: full context (latest.json) or RAG (latest-rag.json)")
    parser.add_argument("--model", help="the configuration to judge (default: default_model in the spec)")
    parser.add_argument("--ops", nargs="+", metavar="FILE", help="ops_eval.py results to use instead of the newest")
    parser.add_argument("--quality", metavar="FILE")
    parser.add_argument("--probes", metavar="FILE")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-scan", action="store_true")
    parser.add_argument("--skip-sandbox", action="store_true")
    parser.add_argument("--out-dir", default=str(HERE), help="where latest.json, latest.md and history/ go")
    args = parser.parse_args()

    spec = yaml.safe_load(SPECS[args.engine].read_text(encoding="utf-8"))
    ev = gather(spec, args)
    card = build(spec, ev)
    out = Path(args.out_dir)
    (out / "history").mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    sfx = "-rag" if args.engine == "rag" else ""
    text = json.dumps(card, indent=2, ensure_ascii=False)
    (out / f"latest{sfx}.json").write_text(text, encoding="utf-8")
    (out / f"history/scorecard{sfx}-{__version__}-{stamp}.json").write_text(text, encoding="utf-8")
    (out / f"latest{sfx}.md").write_text(markdown(card), encoding="utf-8")
    print(f"\n{card['app']} {card['version']} ({ENGINE_NAMES[args.engine]}): {card['decision']}  score {card['total']:.0f}/100")
    for d in card["dimensions"]:
        print(f"  {d['name']:<26}{'n/a' if d['score'] is None else format(d['score'], '.0f'):>5}  (weight {d['weight']}%)")
    for label, key in (("blocked by", "gates_failed"), ("gate not measured", "gates_unmeasured")):
        for name in card[key]:
            print(f"  {label}: {name}")
    print(f"Wrote {out / f'latest{sfx}.json'} and latest{sfx}.md")
    return 0 if card["decision"] != "HOLD" else 1


if __name__ == "__main__":
    sys.exit(main())
