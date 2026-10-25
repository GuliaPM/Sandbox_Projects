"""Offline checks (no API key, no network, no cost):  python -m unittest discover -s tests"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))  # run from a checkout without installing

from meetminer.engine import ConfigError, cost_usd, load_env, load_settings, resolve_prices, with_overrides
from meetminer.transcripts import (
    check_citations, load_meetings, parse_turns, render_corpus, split_multi_citations, title_from_filename,
    ts_to_seconds,
)

SAMPLE = """\
Speaker 1  00:00
Hello there. We can ship that change today. No blockers at all, and the price is $250k.

Speaker 2  00:49
a lot of notifications from the events.

Speaker 1  00:56
Our our meeting was quick. See you at 5:30

Speaker 2  01:10
Okay thanks, that works for me.

Speaker 1  1:02:15
Closing words after an hour.


Transcribed by https://otter.ai
"""


def meetings_from(text: str, name: str = "Test call_transcript.txt"):
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / name).write_text(text, encoding="utf-8")
        (Path(tmp) / "video.mp4").write_bytes(b"x")
        return load_meetings(Path(tmp))  # everything is read into memory, so the folder can go


class ParserTests(unittest.TestCase):
    def test_turns_and_footer(self):
        turns, warnings = parse_turns(SAMPLE)
        self.assertEqual([t.start for t in turns], ["00:00", "00:49", "00:56", "01:10", "1:02:15"])
        self.assertEqual(turns[4].seconds, 3735)
        self.assertEqual(warnings, [])
        self.assertNotIn("otter.ai", turns[-1].text)
        # a text line that merely ends in a time is not a speaker line
        self.assertTrue(turns[2].text.endswith("See you at 5:30"))

    def test_timestamps(self):
        self.assertEqual(ts_to_seconds("05:10"), 310)
        self.assertEqual(ts_to_seconds("1:02:15"), 3735)

    def test_titles(self):
        self.assertEqual(title_from_filename("Quarterly review_ Recording_transcript.txt"), "Quarterly review")
        self.assertEqual(title_from_filename("Weekly sync_transcript.txt"), "Weekly sync")

    def test_load_ignores_non_transcripts(self):
        res = meetings_from(SAMPLE)
        self.assertEqual([m.title for m in res.meetings], ["Test call"])
        self.assertEqual(res.ignored, ["video.mp4"])
        self.assertEqual(res.failed, [])

    def test_unparseable_txt_is_reported(self):
        res = meetings_from("just some notes\nnothing else\n", "notes.txt")
        self.assertEqual(res.meetings, [])
        self.assertEqual(res.failed[0][0], "notes.txt")

    def test_missing_folder(self):
        self.assertTrue(load_meetings(Path("Z:/definitely/not/here")).folder_missing)

    def test_corpus_is_stable(self):
        res = meetings_from(SAMPLE)
        self.assertEqual(render_corpus(res.meetings), render_corpus(res.meetings))
        self.assertIn("[Test call, Speaker 1, 00:56] Our our meeting was quick.", render_corpus(res.meetings))


class LongTurnTests(unittest.TestCase):
    def test_long_turns_repeat_their_tag_and_lose_no_text(self):
        sentence = "This sentence has exactly seven words in it. "
        long_text = (sentence * 40).strip()  # 280 words
        res = meetings_from(f"Speaker 1  00:00\n{long_text}\n\nSpeaker 2  04:00\nShort reply.\n")
        corpus = render_corpus(res.meetings)
        lines = [ln for ln in corpus.splitlines() if ln.startswith("[")]
        tagged = [ln for ln in lines if ln.startswith("[Test call, Speaker 1, 00:00] ")]
        self.assertGreaterEqual(len(tagged), 3)
        self.assertEqual(len([ln for ln in lines if ln.startswith("[Test call, Speaker 2, 04:00] ")]), 1)
        rebuilt = " ".join(ln.split("] ", 1)[1] for ln in tagged)
        self.assertEqual(rebuilt, long_text)


class CitationTests(unittest.TestCase):
    def setUp(self):
        self.res = meetings_from(SAMPLE)
        self.m = self.res.meetings

    def statuses(self, answer):
        return [c.status for c in check_citations(answer, self.m).citations]

    def test_good_quote_is_ok(self):
        self.assertEqual(self.statuses('Agreed: "ship that change today" [Test call, Speaker 1, 00:00].'), ["ok"])

    def test_stutter_and_punctuation_tolerated(self):
        self.assertEqual(self.statuses('"our meeting was quick" [Test call, Speaker 1, 00:56]'), ["ok"])
        self.assertEqual(self.statuses('"the price is $250k" [Test call, Speaker 1, 00:00]'), ["ok"])

    def test_quote_nested_in_quote_and_partial_word_start(self):
        # Found by the live eval: a nested single-quoted phrase, and a quote starting inside "it'll".
        self.assertEqual(self.statuses("\"that works for me\" [Test call, Speaker 2, 01:10]"), ["ok"])
        nested = 'Said "Hello there. \'We can ship that change today\'" [Test call, Speaker 1, 00:00]'
        self.assertEqual(self.statuses(nested), ["ok"])
        res = meetings_from("Speaker 1  00:00\nYeah and it'll be an hour long too, so fine.\n")
        self.assertEqual([c.status for c in check_citations(
            "\"'ll be an hour long too\" [Test call, Speaker 1, 00:00]", res.meetings).citations], ["ok"])

    def test_meeting_and_speaker_names_in_quotes_are_not_quotes(self):
        report = check_citations('See "Test call" and "Speaker 1" [Test call, Speaker 1, 00:00].', self.m)
        self.assertEqual([c.status for c in report.citations], ["ok"])
        self.assertEqual(report.uncited_quotes, [])

    def test_short_uncited_search_terms_are_not_flagged(self):
        report = check_citations('Not found in the transcripts. I looked for "SOC 2" and "HIPAA".', self.m)
        self.assertEqual(report.uncited_quotes, [])
        self.assertTrue(report.clean)

    def test_citation_before_the_quote_is_accepted(self):
        # Found in the desktop app: "Speaker 4 [cite] asks about "the new policy"", with the citation first.
        answer = ('Speaker 2 [Test call, Speaker 2, 01:10] says "that works for me", and Speaker 1 '
                  '[Test call, Speaker 1, 00:00] says "no blockers at all".')
        report = check_citations(answer, self.m)
        self.assertEqual([c.status for c in report.citations], ["ok", "ok"])
        self.assertEqual([len(c.quotes) for c in report.citations], [1, 1])  # each quote sits with its own citation

    def test_citation_before_the_quote_still_catches_a_wrong_turn(self):
        answer = 'Speaker 1 [Test call, Speaker 1, 00:00] says "that works for me".'  # that is in the 01:10 turn
        c = check_citations(answer, self.m).citations[0]
        self.assertEqual(c.status, "weak")
        self.assertEqual(c.found_idx, 3)

    def test_stray_quotation_mark_only_affects_its_own_line(self):
        answer = ('A line with a stray " quote mark [Test call, Speaker 1, 00:00]\n'
                  'Fine: "that works for me" [Test call, Speaker 2, 01:10]')
        report = check_citations(answer, self.m)
        self.assertEqual(report.citations[1].status, "ok")

    def test_two_tags_in_one_bracket_are_split_and_each_checked(self):
        # Found in the app: [A, Speaker 1, 00:00; A, Speaker 2, 01:10]
        answer = ('Said "that works for me" and "no blockers at all" '
                  '[Test call, Speaker 2, 01:10; Test call, Speaker 1, 00:00]')
        fixed = split_multi_citations(answer)
        self.assertTrue(fixed.endswith("[Test call, Speaker 2, 01:10], [Test call, Speaker 1, 00:00]"))
        self.assertEqual([c.status for c in check_citations(fixed, self.m).citations], ["ok", "ok"])
        self.assertEqual(split_multi_citations("a [note; not a tag] b"), "a [note; not a tag] b")  # left alone

    def test_one_letter_quote_does_not_shift_quote_pairing(self):
        # Found by the live eval: ("T") made the next quote start at the closing mark of "T".
        answer = 'The client ("T") said "that works for me" [Test call, Speaker 2, 01:10]'
        report = check_citations(answer, self.m)
        self.assertEqual([c.status for c in report.citations], ["ok"])
        self.assertEqual(report.uncited_quotes, [])

    def test_quote_split_across_two_turns(self):
        # Found by the live eval: Otter ended a turn with "approved by" and began the next with "you".
        res = meetings_from("Speaker 2  02:00\nWe document once the components are ready and approved by\n\n"
                            "Speaker 4  02:05\nyou, yeah agree.\n\nSpeaker 2  02:30\nSomething unrelated.\n")
        quote = '"the components are ready and approved by you"'
        for tag, status in (("Speaker 2, 02:00", "ok"), ("Speaker 4, 02:05", "ok"), ("Speaker 2, 02:30", "weak")):
            report = check_citations(f"{quote} [Test call, {tag}]", res.meetings)
            self.assertEqual(report.citations[0].status, status, tag)

    def test_quote_from_the_neighbouring_turn_is_not_verified(self):
        # Found by using the app: a quote cited under the next turn looked verified but was not in that turn.
        report = check_citations('"Okay thanks that works for me" [Test call, Speaker 1, 00:56]', self.m)
        c = report.citations[0]
        self.assertEqual(c.status, "weak")
        self.assertEqual(c.quotes[0].status, "elsewhere")
        self.assertEqual(c.found_idx, 3)  # the 01:10 turn

    def test_wrong_timestamp_points_to_the_real_turn(self):
        report = check_citations('"meeting was quick" [Test call, Speaker 1, 1:02:15]', self.m)
        c = report.citations[0]
        self.assertEqual(c.status, "weak")
        self.assertEqual(c.found_idx, 2)  # the 00:56 turn

    def test_paraphrase_in_quotes_is_still_flagged(self):
        report = check_citations('He had "no preference" [Test call, Speaker 2, 00:49]', self.m)
        self.assertEqual(report.citations[0].status, "bad")

    def test_pipe_separator_accepted(self):
        self.assertEqual(self.statuses('"no blockers at all" [Test call | Speaker 1 | 00:00]'), ["ok"])

    def test_invented_quote_is_bad(self):
        report = check_citations('He said "we will triple the budget" [Test call, Speaker 1, 00:00].', self.m)
        self.assertEqual(report.citations[0].status, "bad")
        self.assertEqual(report.citations[0].quotes[0].status, "missing")

    def test_quote_from_far_away_turn_is_weak(self):
        report = check_citations('"ship that change today" [Test call, Speaker 1, 1:02:15]', self.m)
        self.assertEqual(report.citations[0].status, "weak")
        self.assertEqual(report.citations[0].quotes[0].status, "elsewhere")

    def test_unknown_meeting_and_timestamp(self):
        self.assertEqual(self.statuses("[Other call, Speaker 1, 00:00]"), ["bad"])
        self.assertEqual(self.statuses("[Test call, Speaker 1, 30:00]"), ["bad"])

    def test_wrong_speaker_is_weak(self):
        self.assertEqual(self.statuses("[Test call, Speaker 2, 00:00]"), ["weak"])

    def test_consecutive_citations_share_quotes(self):
        answer = 'They said "that works for me" [Test call, Speaker 1, 00:00], [Test call, Speaker 2, 01:10]'
        self.assertEqual(self.statuses(answer), ["ok", "ok"])

    def test_uncited_invented_quote_is_flagged(self):
        report = check_citations('Someone said "this was never said anywhere".', self.m)
        self.assertEqual(report.uncited_quotes[0].status, "missing")
        self.assertFalse(report.clean)

    def test_not_found_answer_is_clean(self):
        report = check_citations("Not found in the transcripts. I looked for SOC 2.", self.m)
        self.assertEqual(report.citations, [])
        self.assertTrue(report.clean)


class CostTests(unittest.TestCase):
    TABLE = {  # independent of the shipped default.yaml
        "sonnet": {"input_per_mtok": 2.0, "output_per_mtok": 10.0},
        "opus": {"input_per_mtok": 4.0, "output_per_mtok": 20.0, "cache_read_per_mtok": 0.20},
    }

    def setUp(self):  # never read or write the developer's own settings in .local
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        env = mock.patch.dict(os.environ, {"MEETMINER_HOME": home.name})
        env.start()
        self.addCleanup(env.stop)
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL"):
            os.environ.pop(var, None)

    def test_cost_formula(self):
        prices = resolve_prices(self.TABLE, "sonnet", "5m")
        tokens = {"input": 1000, "cache_write": 0, "cache_read": 10000, "output": 500}
        self.assertAlmostEqual(cost_usd(tokens, prices), 0.009, places=6)
        first = {"input": 200, "cache_write": 10000, "cache_read": 0, "output": 500}
        self.assertAlmostEqual(cost_usd(first, prices), (200 * 2 + 10000 * 2 * 1.25 + 500 * 10) / 1e6, places=6)
        self.assertGreater(cost_usd(first, resolve_prices(self.TABLE, "sonnet", "1h")), cost_usd(first, prices))

    def test_model_specific_cache_read_price(self):
        self.assertAlmostEqual(resolve_prices(self.TABLE, "opus", "5m")["cache_read_per_mtok"], 0.20)
        self.assertAlmostEqual(resolve_prices(self.TABLE, "sonnet", "5m")["cache_read_per_mtok"], 0.20)  # 0.1 x 2.0

    def test_unknown_model_gives_a_clear_error(self):
        with self.assertRaises(ConfigError) as ctx:
            resolve_prices(self.TABLE, "mystery-model", "5m")
        self.assertIn("mystery-model", str(ctx.exception))
        self.assertIn("sonnet", str(ctx.exception))

    def test_config_prices_and_model_switch(self):
        s = load_settings()
        self.assertEqual(s.prices, resolve_prices(s.price_table, s.model, s.cache_ttl))
        other = with_overrides(s, model="claude-sonnet-5-5", effort="medium")
        self.assertEqual((other.model, other.effort), ("claude-sonnet-5-5", "medium"))
        self.assertAlmostEqual(other.prices["input_per_mtok"], 2.0)
        self.assertEqual(s.model, load_settings().model)  # the original is untouched

    def test_model_env_override(self):
        with mock.patch.dict(os.environ, {"ANTHROPIC_MODEL": "claude-opus-5-5"}):
            self.assertEqual(load_settings().model, "claude-opus-5-5")
            self.assertAlmostEqual(load_settings().prices["output_per_mtok"], 20.0)

    def test_env_file_and_notepad_txt_fallback(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MKS_TEST_KEY", None)
            folder = Path(tmp)
            self.assertIsNone(load_env(folder))  # nothing there
            (folder / ".env.txt").write_text("MKS_TEST_KEY=from-txt\n", encoding="utf-8")
            self.assertIn("rename", load_env(folder))
            self.assertEqual(os.environ["MKS_TEST_KEY"], "from-txt")
            os.environ.pop("MKS_TEST_KEY")
            (folder / ".env").write_text("# comment\nMKS_TEST_KEY='from-env'\n", encoding="utf-8")
            self.assertIsNone(load_env(folder))  # .env wins, no warning
            self.assertEqual(os.environ["MKS_TEST_KEY"], "from-env")
            os.environ.pop("MKS_TEST_KEY")

    def test_settings_paths_are_absolute_and_logs_live_in_the_data_folder(self):
        s = load_settings()
        self.assertTrue(s.transcripts_dir.is_absolute())
        self.assertEqual(s.usage_log.parent.resolve(), s.base_dir.resolve())


class SampleTranscriptsTest(unittest.TestCase):
    """The fictional Otter-format transcripts that ship in samples/ must parse cleanly."""

    SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "transcripts"

    def test_samples_parse_without_warnings(self):
        res = load_meetings(self.SAMPLES)
        self.assertEqual(res.failed, [])
        self.assertEqual(sorted(m.title for m in res.meetings), ["Customer onboarding call", "Product planning call"])
        for m in res.meetings:
            self.assertGreater(len(m.turns), 10)
            self.assertEqual(m.warnings, [])
            self.assertGreaterEqual(len(m.speakers), 3)

    def test_the_first_turn_is_long_like_a_real_export(self):
        planning = next(m for m in load_meetings(self.SAMPLES).meetings if m.title == "Product planning call")
        self.assertGreater(len(planning.turns[0].text.split()), 200)


if __name__ == "__main__":
    unittest.main()
