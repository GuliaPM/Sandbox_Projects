"""Corpora of different sizes for the speed and cost evals (OPS-01).

Cell S is the two fictional sample calls. Cells M (about 15,000 tokens), L (about 60,000, the size at which the app
starts to warn) and XL (about 150,000, some twenty one-hour calls) add synthetic filler meetings in Otter's format. The filler is deliberately boring and never touches the
facts the eval questions ask about (dates, amounts, names, "launch", "beta", "budget"...), so the expected answers do not
change. It is deterministic: the same size always gives the same files.
"""
from __future__ import annotations

import random
import shutil
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples" / "transcripts"
# S is whatever the samples measure (about 3,900). XL is about 20 one-hour calls, the scenario RAG is for.
TARGET_TOKENS = {"S": 0, "M": 15_000, "L": 60_000, "XL": 150_000}

TITLES = ["Facilities review", "Hiring sync", "Month end close", "Support triage", "Travel policy review",
          "Office move planning", "Event planning", "Procurement check-in", "Wellness program", "Recruiting pipeline",
          "Inventory count", "Documentation cleanup", "Parking and badges", "Supplier scorecards", "Holiday coverage",
          "Warehouse safety", "Newsletter planning", "Equipment refresh"]
NAMES = ["Avery", "Blake", "Cameron", "Devon", "Emery", "Frankie", "Harper", "Indigo", "Jules", "Kai", "Lennox",
         "Morgan", "Noel", "Oakley", "Parker", "Quinn", "Reese", "Sage", "Tatum", "Wren"]
THINGS = ["the parking permits", "the badge readers", "the third floor kitchen", "the cleaning schedule",
          "the recycling pickup", "the visitor sign-in desk", "the supply cabinet", "the shared printers",
          "the meeting room screens", "the bike storage", "the mailroom shelves", "the loading dock", "the lobby plants",
          "the conference phones", "the laptop refresh list", "the onboarding checklist for interns"]
VERBS = ["review", "tidy up", "re-order", "document", "count", "label", "move", "replace", "photograph", "audit"]
MONTHS = ["March", "April", "May", "June"]
AMOUNTS = ["$2,980", "$5,640", "$7,350", "$9,115", "$3,275", "$6,480", "$8,790"]
FILLERS = ["Right, that makes sense.", "Okay, I will note that.", "Yeah, I agree with that.", "Let me check and come back.",
           "That sounds about right to me.", "I would not rush it, honestly.", "We can revisit it next time.",
           "Does anyone object to that?", "Good, then we are aligned.", "Thanks, that helps a lot."]
NUMBERS = [n for n in range(51, 98) if n not in (55, 77)]

# --distractors hard: filler meetings that also carry NEAR-MISS facts (another project's launch date, another budget,
# another vendor's SOC report, other people's action items, other seat prices). Each one differs from the facts the eval
# questions ask about (other months, other amounts, other names, other meeting titles), so every expected answer stays
# unique to its sample meeting and the cited file still decides whether an answer is right. The titles avoid "planning"
# and "onboarding" because expect_files matches those words in file names. Nothing here touches the not-found traps
# (competitors, Kubernetes, hiring plans, contract renewals).
HARD_TITLES = ["Supplier review", "Pilot readiness", "Pricing committee", "Release readiness", "Partner kickoff",
               "Warehouse app sync", "Vendor security check", "Store rollout", "Scanner program", "Label printer project"]
VENDORS = ["Northwind Parcel", "Bluefin Labels", "Copperline Scanners", "Tallgrass Freight", "Pinecrest Payroll"]
PROJECTS = ["Project Beacon", "Project Ridgeway", "Project Larkspur", "Project Saltmarsh"]
HARD_MONTHS = ["December", "January", "February"]
HARD_AMOUNTS = ["$14,500", "$16,000", "$21,000", "$9,500", "$27,000"]  # never $12,000 or $18,000
SEAT_PRICES = [29, 32, 35, 38]  # never 25
PREPAY_OFF = [5, 12, 15]  # never 10
PILOT_SIZES = [30, 35, 45, 60]  # never 25
SPECS = ["billing API", "inventory API", "label service API", "returns API"]


def _near_miss(rng: random.Random) -> str:
    who, other = rng.sample(NAMES, 2)
    vendor, project, thing = rng.choice(VENDORS), rng.choice(PROJECTS), rng.choice(THINGS)
    month, day = rng.choice(HARD_MONTHS), rng.choice([d for d in range(2, 29) if d not in (14, 30)])
    kind = rng.randrange(9)
    if kind == 0:
        return f"So the {project} launch moves to {month} {day}, because the {vendor} integration slipped again."
    if kind == 1:
        return (f"For contractor help on {thing} we budgeted {rng.choice(HARD_AMOUNTS)}, and finance is fine with it as "
                f"long as we stay under that.")
    if kind == 2:
        return f"The {vendor} pilot starts with {rng.choice(PILOT_SIZES)} stores, and the invites go out on {month} {day}."
    if kind == 3:
        return f"{who} owns the {rng.choice(SPECS)} spec and will deliver the first complete version by {month} {day}."
    if kind == 4:
        return (f"{vendor} quoted ${rng.choice(SEAT_PRICES)} per seat per month, with {rng.choice(PREPAY_OFF)}% off if we "
                f"prepay the full year.")
    if kind == 5:
        return (f"Before we sign with {vendor}, security wants their SOC 2 Type I report and a penetration test summary.")
    if kind == 6:
        return f"{who} will finish the {thing} mockups by {month} {day}, and {other} drafts the announcement email."
    if kind == 7:
        return f"{vendor} asked for our SOC 1 report before their finance team signs anything, so {who} will send it."
    return (f"Action items for {project}: {who} sends the scope document, {other} books the training, and the rollout "
            f"review is on {month} {day}.")


def make_hard_meeting(index: int, words: int = 700) -> tuple[str, str]:
    """A filler meeting with near-miss facts: like make_meeting, plus one near-miss sentence in most turns."""
    rng = random.Random(index * 104729 + 71)
    title = f"{HARD_TITLES[index % len(HARD_TITLES)]}" + (f" {index // len(HARD_TITLES) + 1}" if index >= len(HARD_TITLES) else "")
    turns, seconds, total = [], 0, 0
    while total < words:
        speaker = rng.randrange(1, 5)
        sentences = [_sentence(rng) for _ in range(rng.randrange(3, 6))]
        if rng.random() < 0.7:
            sentences.insert(rng.randrange(len(sentences) + 1), _near_miss(rng))
        text = " ".join(sentences)
        turns.append(f"Speaker {speaker}  {seconds // 60:02d}:{seconds % 60:02d}\n{text}")
        seconds += max(25, len(text.split()) // 2)
        total += len(text.split())
    return f"{title} call_transcript.txt", "\n\n".join(turns) + "\n\n\nTranscribed by https://otter.ai\n"


def _sentence(rng: random.Random) -> str:
    who, other = rng.sample(NAMES, 2)
    thing, verb = rng.choice(THINGS), rng.choice(VERBS)
    month, day = rng.choice(MONTHS), rng.choice(range(2, 29))
    kind = rng.randrange(8)
    if kind == 0:
        return f"{who} will {verb} {thing} before {month} {day}."
    if kind == 1:
        return f"We counted {rng.choice(NUMBERS)} items near {thing}, which is {rng.choice(NUMBERS)} fewer than last quarter."
    if kind == 2:
        return f"{other} said the quote for {thing} was {rng.choice(AMOUNTS)}, and {who} wants a second quote."
    if kind == 3:
        return f"The average wait was {rng.choice(NUMBERS)} minutes, so {who} asked whether we should {verb} {thing} sooner."
    if kind == 4:
        return f"Um, I think {thing} is fine for now, but {other} would like to {verb} it in {month}."
    if kind == 5:
        return f"There were {rng.choice(NUMBERS)} requests this month about {thing}, mostly from the second floor."
    if kind == 6:
        return rng.choice(FILLERS)
    return f"{who}, can you {verb} {thing} and tell {other} when it is done? I would like that by {month} {day}."


def make_meeting(index: int, words: int = 700) -> tuple[str, str]:
    """(file name, text) of one filler meeting in Otter's .txt layout."""
    rng = random.Random(index * 7919 + 13)
    title = f"{TITLES[index % len(TITLES)]}" + (f" {index // len(TITLES) + 1}" if index >= len(TITLES) else "")
    turns, seconds, total = [], 0, 0
    while total < words:
        speaker = rng.randrange(1, 5)
        text = " ".join(_sentence(rng) for _ in range(rng.randrange(4, 8)))
        turns.append(f"Speaker {speaker}  {seconds // 60:02d}:{seconds % 60:02d}\n{text}")
        seconds += max(25, len(text.split()) // 2)
        total += len(text.split())
    return f"{title} call_transcript.txt", "\n\n".join(turns) + "\n\n\nTranscribed by https://otter.ai\n"


DISTRACTORS = ("easy", "hard")


def build_cell(cell: str, workdir: Path, measure: Callable[[Path], int] | None = None,
               distractors: str = "easy") -> Path:
    """The folder of transcripts for cell S, M, L or XL. For the others the filler is added to a copy of the samples, then
    `measure(folder)` (prompt tokens) is used, if given, to bring the size within about 10% of the target.
    distractors="hard" uses filler with near-miss facts (make_hard_meeting); "easy" is the original filler, unchanged."""
    cell = cell.upper()
    if cell not in TARGET_TOKENS:
        raise ValueError(f"unknown cell {cell!r}; use S, M, L or XL")
    if distractors not in DISTRACTORS:
        raise ValueError(f"unknown distractors {distractors!r}; use easy or hard")
    if cell == "S":
        return SAMPLES
    make = make_hard_meeting if distractors == "hard" else make_meeting
    folder = workdir / (f"cell-{cell}" if distractors == "easy" else f"cell-{cell}-hard")
    target = TARGET_TOKENS[cell]
    count = max(1, round((target - 3_900) / 1_500))  # a first guess: about 1,500 tokens per filler meeting
    for _ in range(4):
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        for f in SAMPLES.glob("*.txt"):
            shutil.copy2(f, folder / f.name)
        for i in range(count):
            name, text = make(i)
            (folder / name).write_text(text, encoding="utf-8")
        if measure is None:
            break
        tokens = measure(folder)
        if abs(tokens - target) <= 0.1 * target:
            break
        per = max((tokens - 3_900) / count, 200)
        count = max(1, round((target - 3_900) / per))
    return folder
