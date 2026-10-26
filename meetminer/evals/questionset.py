"""Loading an eval question file, for run_eval.py, ops_eval.py and retrieval_eval.py (--questions).

evals/questions.yaml is the original set (the development set the retriever was built on); evals/heldout/questions.yaml
is the frozen held-out set. A question may name another with `follows: <id>`: it is then asked with that question and
its answer as the previous turn of the conversation, so the runners ask questions in file order.
"""
from __future__ import annotations

from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ORIGINAL = HERE / "questions.yaml"
HELDOUT = HERE / "heldout" / "questions.yaml"


def load(path: Path | str | None = None) -> list[dict]:
    questions = yaml.safe_load(Path(path or ORIGINAL).read_text(encoding="utf-8")) or []
    seen: set[str] = set()
    for q in questions:
        parent = q.get("follows")
        if parent and parent not in seen:
            raise ValueError(f"{q['id']} follows {parent!r}, which is not an earlier question in the file")
        seen.add(q["id"])
    return questions


def label(path: Path | str | None = None) -> str:
    """'original', 'heldout', or the file's own name for any other question file."""
    p = Path(path or ORIGINAL).resolve()
    return {ORIGINAL.resolve(): "original", HELDOUT.resolve(): "heldout"}.get(p, p.stem)


def only(questions: list[dict], ids: str | None) -> list[dict]:
    """The questions named in a comma-separated list, plus the ones they follow up (in file order)."""
    if not ids:
        return questions
    wanted = set(ids.split(","))
    by_id = {q["id"]: q for q in questions}
    for qid in list(wanted):
        while qid in by_id and by_id[qid].get("follows"):
            qid = by_id[qid]["follows"]
            wanted.add(qid)
    return [q for q in questions if q["id"] in wanted]


def history_for(q: dict, asked: dict[str, tuple[str, str]]) -> list[tuple[str, str]]:
    """The conversation before this question: [(previous question, its answer)] for a follow-up, else []."""
    parent = q.get("follows")
    if not parent:
        return []
    if parent not in asked:
        raise ValueError(f"{q['id']} follows {parent!r}, which has not been asked in this run")
    return [asked[parent]]
