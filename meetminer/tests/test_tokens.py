"""Checks for the token estimate (no network, no key, no cost):  python -m unittest discover -s tests

transcripts.estimate_tokens is the one place the app estimates tokens. It was calibrated against the count_tokens
endpoint (evals/token_count.py, evals/TOKENS.md); the RAG budget (rag_max_tokens) is in real tokens.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import anthropic

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))  # run from a checkout without installing
sys.path.insert(0, str(ROOT / "evals"))

import corpus  # noqa: E402
import questionset  # noqa: E402
from meetminer.engine import MeetingQA, build_system, get_context, load_settings  # noqa: E402
from meetminer.retrieval import Retriever  # noqa: E402
from meetminer.transcripts import CHARS_PER_TOKEN, estimate_tokens, load_meetings  # noqa: E402

RESULTS = ROOT / "evals" / "results"


def newest_token_counts() -> dict | None:
    files = sorted(RESULTS.glob("tokens-*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


class EstimateTests(unittest.TestCase):
    def test_the_estimate_rounds_up(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("x"), 1)
        self.assertEqual(estimate_tokens("x" * 27), round(27 / CHARS_PER_TOKEN))

    def test_the_estimate_is_never_below_a_measured_count(self):
        """Every prompt and every RAG excerpt measured by evals/token_count.py: the estimate errs high, not low."""
        data = newest_token_counts()
        if data is None:
            self.skipTest("no evals/results/tokens-*.json yet")
        for model, m in data["models"].items():
            for cell, r in m["cells"].items():
                with self.subTest(model=model, cell=cell):
                    self.assertGreaterEqual(estimate_tokens("x" * r["chars"]), r["actual"])
                    self.assertLess(estimate_tokens("x" * r["chars"]), r["actual"] * 1.15)  # and not wildly high
            for qid, r in m["rag"].items():
                if not r.get("no_match"):
                    with self.subTest(model=model, question=qid):
                        self.assertGreaterEqual(estimate_tokens("x" * r["excerpt_chars"]), r["excerpt_actual"])

    def test_without_a_key_the_corpus_size_uses_the_calibrated_estimate(self):
        meetings = load_meetings(corpus.SAMPLES).meetings
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"MEETMINER_HOME": tmp}):
            settings = replace(load_settings(), transcripts_dir=corpus.SAMPLES)
            client = mock.Mock()
            client.messages.count_tokens.side_effect = anthropic.APIConnectionError(request=mock.Mock())
            qa = MeetingQA(settings, meetings, client=client)
            tokens, how = qa.count_corpus_tokens()
        text = build_system(get_context(meetings, "", []), settings.cache_ttl)[0]["text"]
        self.assertEqual((tokens, how), (estimate_tokens(text), "estimated"))
        self.assertGreater(tokens, len(text) // 4)  # the old rule undercounted


class RagBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.meetings = load_meetings(corpus.build_cell("XL", Path(cls.tmp.name))).meetings
        cls.questions = questionset.load()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_excerpts_stay_within_the_budget_in_estimated_real_tokens(self):
        for budget in (1500, 6000):
            retriever = Retriever(self.meetings, 12, 1, budget)
            for q in self.questions:
                got = retriever.retrieve(q["question"])
                if len(got.turns) > 1:  # the first passage always goes in, even if it is long on its own
                    with self.subTest(budget=budget, question=q["id"]):
                        self.assertLessEqual(got.tokens_estimate, budget)
                        self.assertEqual(got.tokens_estimate, estimate_tokens(got.text))

    def test_a_bigger_budget_sends_more(self):
        q = "What documents did the customer ask for before signing?"
        small = Retriever(self.meetings, 12, 1, 1500).retrieve(q)
        big = Retriever(self.meetings, 12, 1, 6000).retrieve(q)
        self.assertLess(small.tokens_estimate, big.tokens_estimate)


if __name__ == "__main__":
    unittest.main()
