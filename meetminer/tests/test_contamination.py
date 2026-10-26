"""Checks for the eval-contamination fixes (no network, no key, no cost):  python -m unittest discover -s tests

The frozen held-out set, the audited synonym list, the near-miss ("hard") filler and the --questions plumbing.
See evals/CONTAMINATION.md.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))  # run from a checkout without installing
sys.path.insert(0, str(ROOT / "evals"))

import corpus  # noqa: E402
import questionset  # noqa: E402
from meetminer.retrieval import SYNONYMS  # noqa: E402
from meetminer.transcripts import load_meetings  # noqa: E402

HELDOUT = ROOT / "evals" / "heldout"


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look their module up while the class is built
    spec.loader.exec_module(module)
    return module


retrieval_mod = load_script("retrieval_eval_c", ROOT / "evals" / "retrieval_eval.py")
card_mod = load_script("build_scorecard_c", ROOT / "scorecards" / "build_scorecard.py")


class HeldOutSetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.questions = questionset.load(questionset.HELDOUT)

    def test_the_held_out_file_is_frozen(self):
        """Changing the held-out questions needs a deliberate update of FROZEN.sha256, visible in review. Line endings
        are normalised so a Windows checkout gives the same hash."""
        data = (HELDOUT / "questions.yaml").read_bytes().replace(b"\r\n", b"\n")
        recorded = (HELDOUT / "FROZEN.sha256").read_text(encoding="utf-8").split()[0]
        self.assertEqual(hashlib.sha256(data).hexdigest(), recorded,
                         "evals/heldout/questions.yaml changed: it is frozen (see its header)")

    def test_the_held_out_set_has_the_required_shape(self):
        kinds = [q["kind"] for q in self.questions]
        self.assertTrue(10 <= len(self.questions) <= 12)
        self.assertGreaterEqual(kinds.count("not_in_data"), 2)
        self.assertGreaterEqual(kinds.count("comparison"), 1)
        self.assertGreaterEqual(sum(1 for q in self.questions if q.get("follows")), 2)
        for q in self.questions:
            self.assertEqual(bool(q.get("answer_turns")), not q.get("not_found"), q["id"])

    def test_every_answer_turn_exists_in_the_samples(self):
        meetings = {m.title: {t.start for t in m.turns} for m in load_meetings(corpus.SAMPLES).meetings}
        for q in self.questions:
            for turn in q.get("answer_turns", []):
                title, start = turn.split("|")
                self.assertIn(start, meetings.get(title, set()), f"{q['id']}: {turn}")

    def test_held_out_questions_avoid_the_original_wording(self):
        words = ("security", "document", "signing", "budget", "launch", "beta", "invitation", "discount", "prepay",
                 "price", "seat", "api spec", "action item", "dark mode", "single sign", "soc", "contractor", "qa ")
        for q in self.questions:
            for word in words:
                self.assertNotIn(word, q["question"].lower() + " ", f"{q['id']} uses {word!r}")


class SynonymAuditTests(unittest.TestCase):
    REMOVED = {"price": ["per"], "document": ["report", "agreement", "contract"],
               "documents": ["report", "agreement", "contract"], "owns": ["deliver"], "security": ["soc", "review"],
               "discount": ["prepay"], "invitations": ["invites", "invite"], "signing": ["sign", "signature"]}

    def test_the_eval_derived_synonyms_stay_removed(self):
        for word, gone in self.REMOVED.items():
            for alt in gone:
                self.assertNotIn(alt, SYNONYMS.get(word, []), f"{word} -> {alt} is eval-derived (CONTAMINATION.md)")

    def test_the_pre_audit_list_only_adds_to_the_current_one(self):
        """The before/after comparison removes entries and changes nothing else."""
        for word, alts in SYNONYMS.items():
            self.assertTrue(set(alts) <= set(retrieval_mod.PRE_AUDIT_SYNONYMS[word]), word)


class QuestionSetTests(unittest.TestCase):
    def test_labels_and_the_original_file(self):
        self.assertEqual(questionset.label(None), "original")
        self.assertEqual(questionset.label(questionset.HELDOUT), "heldout")
        self.assertEqual(len(questionset.load()), 10)

    def test_a_follow_up_must_come_after_the_question_it_follows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "q.yaml"
            path.write_text("- {id: b, kind: lookup, follows: a, question: x}\n- {id: a, kind: lookup, question: y}\n",
                            encoding="utf-8")
            with self.assertRaises(ValueError):
                questionset.load(path)

    def test_only_keeps_the_questions_a_follow_up_needs(self):
        qs = questionset.load(questionset.HELDOUT)
        picked = [q["id"] for q in questionset.only(qs, "ho-login-person")]
        self.assertEqual(picked, ["ho-login-tech", "ho-login-person"])

    def test_a_follow_up_is_asked_with_the_previous_turn(self):
        q = {"id": "b", "follows": "a"}
        self.assertEqual(questionset.history_for(q, {"a": ("first?", "answer")}), [("first?", "answer")])
        self.assertEqual(questionset.history_for({"id": "a"}, {}), [])
        with self.assertRaises(ValueError):
            questionset.history_for(q, {})


class HardDistractorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = " ".join(corpus.make_hard_meeting(i)[1] for i in range(40)).lower()
        cls.names = " ".join(corpus.make_hard_meeting(i)[0] for i in range(40)).lower()

    def test_the_hard_filler_is_deterministic_and_in_otter_format(self):
        self.assertEqual(corpus.make_hard_meeting(5), corpus.make_hard_meeting(5))
        self.assertRegex(corpus.make_hard_meeting(5)[1], r"(?m)^Speaker \d  \d\d:\d\d$")

    def test_it_carries_near_miss_facts(self):
        for phrase in ("launch moves to", "contractor help", "per seat per month", "soc 2 type i report",
                       "pilot starts with", "spec and will deliver", "mockups by", "action items for"):
            self.assertIn(phrase, self.text, phrase)

    def test_every_expected_answer_stays_unique_to_its_sample_meeting(self):
        for fact in ("$18,000", "$12,000", "november", "october", "25 customers", "$25 per seat", "10% off", "10,800",
                     "saml", "kofi", "marcus", "marcos", "lighthouse", "brightwater", "data processing", "type ii",
                     "rollback", "dark mode", "two days a week", "kubernetes", "competitor", "renew", "engineer"):
            self.assertNotIn(fact, self.text, fact)

    def test_file_names_cannot_satisfy_expect_files(self):
        self.assertNotIn("planning", self.names)
        self.assertNotIn("onboarding", self.names)

    def test_the_easy_filler_is_unchanged_from_1_2_0(self):
        """Earlier results stay comparable: the original filler is byte-for-byte what 1.2.0 generated."""
        h = hashlib.sha256()
        for i in range(97):
            name, text = corpus.make_meeting(i)
            h.update(name.encode())
            h.update(text.encode())
        self.assertEqual(h.hexdigest(), "d439beb3f27bf262957b6ccc8ceac5ebfc89304b6781a31ac58c289c522d13f2")

    def test_hard_cells_live_in_their_own_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            hard = corpus.build_cell("M", Path(tmp), distractors="hard")
            easy = corpus.build_cell("M", Path(tmp))
            self.assertEqual((hard.name, easy.name), ("cell-M-hard", "cell-M"))
            self.assertIn("Product planning call_transcript.txt", {p.name for p in hard.glob("*.txt")})
            self.assertFalse(load_meetings(hard).failed)
            with self.assertRaises(ValueError):
                corpus.build_cell("M", Path(tmp), distractors="medium")


class RetrievalEvalVariantTests(unittest.TestCase):
    def test_result_files_name_what_differs_from_the_default(self):
        self.assertEqual(retrieval_mod.variant("original", "easy", "current"), "")
        self.assertEqual(retrieval_mod.variant("heldout", "hard", "current"), "heldout-hard")
        self.assertEqual(retrieval_mod.variant("original", "easy", "pre-audit"), "pre-audit")

    def test_follow_ups_are_retrieved_with_the_previous_question(self):
        qs = questionset.only(questionset.load(questionset.HELDOUT), "ho-login-person")
        with tempfile.TemporaryDirectory() as tmp:
            r = retrieval_mod.evaluate_cell("S", qs, Path(tmp))
        self.assertEqual([p["follows"] for p in r["per_question"]], [None, "ho-login-tech"])

    def test_the_scorecard_picks_retrieval_files_by_what_they_measured(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(card_mod, "RESULTS", Path(tmp)):
            def write(name, **meta):
                path = Path(tmp) / name
                path.write_text(json.dumps({"cells": {}, **meta}), encoding="utf-8")
                return path
            old = write("retrieval-20260101-000000.json")  # written before the fields existed: original, easy
            held = write("retrieval-heldout-20260102-000000.json", questions_set="heldout", distractors="easy",
                         synonyms="current")
            write("retrieval-pre-audit-20260103-000000.json", questions_set="original", distractors="easy",
                  synonyms="pre-audit")
            write("retrieval-hard-20260104-000000.json", questions_set="original", distractors="hard", synonyms="current")
            for i, p in enumerate(sorted(Path(tmp).iterdir())):
                os.utime(p, (time.time() + i, time.time() + i))  # newest = latest date in the name
            self.assertEqual(card_mod.newest_retrieval(), old)
            self.assertEqual(card_mod.newest_retrieval("heldout"), held)
            self.assertIsNone(card_mod.newest_retrieval("heldout", "hard"))

    def test_held_out_rows_are_information_only(self):
        for engine in ("full", "rag"):
            import yaml
            spec = yaml.safe_load(card_mod.SPECS[engine].read_text(encoding="utf-8"))
            rows = [m for d in spec["dimensions"] for m in d["metrics"] if m["source"].startswith("heldout.")]
            self.assertGreaterEqual(len(rows), 2, engine)
            self.assertTrue(all(m.get("info") and not m.get("gate") for m in rows), engine)


if __name__ == "__main__":
    unittest.main()
