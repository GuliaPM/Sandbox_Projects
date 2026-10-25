"""Checks for the guardrails built into the app (no network, no key):  python -m unittest discover -s tests

    GR-REL-03  request timeout          GR-COST-05  daily and monthly limits
    GR-COST-06 model-switch multiple    GR-INT-04   numbers and dates against the cited turn
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import anthropic

try:  # the Anthropic SDK 2.x builds its errors from httpx2 objects; older ones used httpx
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))  # run from a checkout without installing

from meetminer import guardrails, storage, usagelog  # noqa: E402
from meetminer.engine import MeetingQA, load_settings  # noqa: E402
from meetminer.transcripts import check_citations, load_meetings  # noqa: E402

REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
PLANNING = "Product planning call"
ONBOARDING = "Customer onboarding call"


def reply(text: str, stop: str = "end_turn", output: int = 60) -> SimpleNamespace:
    usage = SimpleNamespace(input_tokens=100, cache_creation_input_tokens=0, cache_read_input_tokens=2000,
                            output_tokens=output)
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop, usage=usage)


class FakeClient:
    """Stands in for anthropic.Anthropic: each call to messages.create returns or raises the next prepared item."""

    def __init__(self, *items):
        self.items = list(items)
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        item = self.items.pop(0) if len(self.items) > 1 else self.items[0]
        if isinstance(item, Exception):
            raise item
        return item


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        env = mock.patch.dict(os.environ, {"MEETMINER_HOME": self.home.name})
        env.start()
        self.addCleanup(env.stop)
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL"):
            os.environ.pop(var, None)
        self.addCleanup(self.home.cleanup)
        self.settings = replace(load_settings(), transcripts_dir=ROOT / "samples" / "transcripts")
        self.meetings = load_meetings(self.settings.transcripts_dir).meetings

    def qa(self, *items) -> tuple[MeetingQA, FakeClient]:
        client = FakeClient(*items)
        return MeetingQA(self.settings, self.meetings, client=client), client

    def log_rows(self) -> list[dict]:
        return usagelog.read_rows(self.settings.usage_log)

    def write_log(self, rows: list[tuple[datetime, float]]) -> None:
        for when, cost in rows:
            usagelog.append(self.settings.usage_log, {
                "time": when.isoformat(timespec="seconds"), "model": "claude-haiku-5-5", "effort": "low", "input": 1,
                "cache_write": 0, "cache_read": 0, "output": 1, "cost_usd": f"{cost:.6f}", "seconds": "1.0",
                "question": "earlier", "status": "ok", "error_kind": ""})


# --------------------------------------------------------------------------- usage log


class UsageLogTests(Base):
    def test_an_old_log_without_status_columns_is_upgraded_in_place(self):
        path = self.settings.usage_log
        path.write_text("time,model,effort,input,cache_write,cache_read,output,cost_usd,seconds,question\n"
                        "2026-10-01T09:00:00,claude-haiku-5-5,low,10,0,2000,50,0.000300,1.5,old question\n",
                        encoding="utf-8")
        self.write_log([(datetime(2026, 10, 2, 9, 0), 0.01)])
        rows = usagelog.read_rows(path)
        self.assertEqual([r["status"] for r in rows], ["ok", "ok"])
        self.assertEqual(rows[0]["question"], "old question")
        self.assertEqual(path.read_text(encoding="utf-8").splitlines()[0], ",".join(usagelog.COLUMNS))

    def test_damaged_lines_are_skipped_not_raised(self):
        path = self.settings.usage_log
        self.write_log([(datetime(2026, 10, 2, 9, 0), 0.01)])
        with path.open("a", encoding="utf-8") as f:
            f.write("not a date,m,e,x,y,z,w,cost,sec,q,ok,\n")
        self.assertEqual(len(usagelog.read_rows(path)), 1)

    def test_a_missing_log_reads_as_empty(self):
        self.assertEqual(usagelog.read_rows(self.home_path("nothing.csv")), [])

    def home_path(self, name: str) -> Path:
        return Path(self.home.name) / name


# --------------------------------------------------------------------------- GR-COST-05


class SpendLimitTests(Base):
    NOW = datetime(2026, 10, 25, 15, 0)

    def test_today_and_month_are_summed_from_the_log(self):
        self.write_log([(self.NOW - timedelta(hours=1), 0.40), (self.NOW - timedelta(days=1), 1.00),
                        (datetime(2026, 9, 30, 12, 0), 9.00)])  # last month: counts for neither
        status = guardrails.spend_status(replace(self.settings, max_daily_cost_usd=1, max_monthly_cost_usd=10), self.NOW)
        self.assertAlmostEqual(status.today, 0.40)
        self.assertAlmostEqual(status.month, 1.40)
        self.assertFalse(status.daily_reached or status.monthly_reached)

    def test_daily_limit_reached(self):
        self.write_log([(self.NOW - timedelta(minutes=5), 1.20)])
        kind, message = guardrails.spend_limit_error(replace(self.settings, max_daily_cost_usd=1.0), self.NOW)
        self.assertEqual(kind, "daily_cap")
        self.assertIn("$1.20 of $1.00 today", message)

    def test_monthly_limit_reached_even_when_today_is_fine(self):
        self.write_log([(self.NOW - timedelta(days=5), 3.0), (self.NOW, 0.1)])
        kind, message = guardrails.spend_limit_error(
            replace(self.settings, max_daily_cost_usd=1.0, max_monthly_cost_usd=3.0), self.NOW)
        self.assertEqual(kind, "monthly_cap")
        self.assertIn("this month", message)

    def test_zero_means_no_limit(self):
        self.write_log([(self.NOW, 500.0)])
        settings = replace(self.settings, max_daily_cost_usd=0, max_monthly_cost_usd=0)
        self.assertIsNone(guardrails.spend_limit_error(settings, self.NOW))

    def test_the_limit_blocks_the_call_and_survives_a_restart(self):
        self.write_log([(datetime.now() - timedelta(seconds=30), 2.0)])
        self.settings = replace(self.settings, max_daily_cost_usd=1.0)
        for _ in range(2):  # a fresh MeetingQA and CostTracker each time: nothing is remembered in memory
            qa, client = self.qa(reply("never used"))
            ans = qa.ask("What is the budget?")
            self.assertEqual(ans.error_kind, "daily_cap")
            self.assertEqual(client.calls, 0)

    def test_limits_chosen_in_settings_win_and_zero_is_kept(self):
        storage.update_user_settings(max_daily_cost_usd=0, max_monthly_cost_usd=7.5)
        loaded = load_settings()
        self.assertEqual(loaded.max_daily_cost_usd, 0.0)
        self.assertEqual(loaded.max_monthly_cost_usd, 7.5)

    def test_defaults_ship_with_sensible_limits(self):
        loaded = load_settings()
        self.assertEqual((loaded.max_daily_cost_usd, loaded.max_monthly_cost_usd), (5.0, 20.0))


# --------------------------------------------------------------------------- GR-REL-03 and failure handling


class FailureTests(Base):
    def test_a_timeout_is_reported_as_one_and_logged_as_a_failure(self):
        qa, client = self.qa(anthropic.APITimeoutError(request=REQUEST))
        ans = qa.ask("When is the launch?")
        self.assertEqual(ans.error_kind, "timeout")
        self.assertIn("did not answer within 60 seconds", ans.error)
        self.assertEqual(qa.tracker.total, 0.0)
        row = self.log_rows()[0]
        self.assertEqual((row["status"], row["error_kind"], row["cost_usd"]), ("failed", "timeout", 0.0))

    def test_a_dropped_connection_is_not_called_a_timeout(self):
        qa, _ = self.qa(anthropic.APIConnectionError(request=REQUEST))
        ans = qa.ask("When is the launch?")
        self.assertEqual(ans.error_kind, "network")
        self.assertIn("internet connection", ans.error)

    def test_other_failures_get_their_own_kind(self):
        def status_error(cls, code, message="x", headers=None):
            return cls(message, response=httpx.Response(code, request=REQUEST, headers=headers or {}), body=None)

        cases = [
            (status_error(anthropic.AuthenticationError, 401), "auth", "key was rejected"),
            (status_error(anthropic.RateLimitError, 429, headers={"retry-after": "7"}), "rate_limit", "7 s"),
            (status_error(anthropic.InternalServerError, 500, "oops"), "api", "API error 500"),
            (status_error(anthropic.BadRequestError, 400, "Your credit balance is too low"), "credit", "credit balance"),
        ]
        for exc, kind, fragment in cases:
            with self.subTest(kind=kind):
                qa, _ = self.qa(exc)
                ans = qa.ask("q")
                self.assertEqual(ans.error_kind, kind)
                self.assertIn(fragment, ans.error)
        self.assertEqual([r["error_kind"] for r in self.log_rows()], [k for _, k, _ in cases])

    def test_user_fixable_failures_are_listed(self):
        from meetminer.engine import USER_FIXABLE
        self.assertTrue({"auth", "credit", "daily_cap"} <= USER_FIXABLE)
        self.assertFalse({"timeout", "network", "rate_limit", "api"} & USER_FIXABLE)

    def test_the_client_is_built_with_the_configured_timeout_and_retries(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-api03-FAKE-for-the-client-test-0123456789"
        qa = MeetingQA(replace(self.settings, request_timeout_seconds=12.0, max_retries=1), self.meetings)
        self.assertEqual(qa.client.timeout, 12.0)
        self.assertEqual(qa.client.max_retries, 1)

    def test_the_shipped_defaults_are_a_minute_and_two_retries(self):
        self.assertEqual((self.settings.request_timeout_seconds, self.settings.max_retries), (60.0, 2))

    def test_a_good_answer_is_logged_ok_and_counts_toward_the_session(self):
        text = f'The price is $25 per seat per month [{ONBOARDING}, Speaker 1, 00:00].'
        qa, _ = self.qa(reply(text))
        ans = qa.ask("What is the price?")
        self.assertEqual(ans.error, "")
        self.assertEqual(self.log_rows()[0]["status"], "ok")
        self.assertEqual(qa.tracker.calls, 1)

    def test_a_cut_off_answer_is_logged_as_truncated_and_an_empty_one_as_a_failure(self):
        qa, _ = self.qa(reply(f"Part of it [{ONBOARDING}, Speaker 1, 00:00]", stop="max_tokens"))
        ans = qa.ask("q")
        self.assertEqual(ans.error, "")
        qa2, _ = self.qa(reply("", stop="max_tokens"))
        self.assertEqual(qa2.ask("q").error_kind, "truncation")
        rows = self.log_rows()
        self.assertEqual([(r["status"], r["error_kind"]) for r in rows], [("ok", "truncated"), ("failed", "truncation")])

    def test_a_bug_in_the_number_check_does_not_cost_the_user_the_answer(self):
        qa, _ = self.qa(reply(f"The price is $25 [{ONBOARDING}, Speaker 1, 00:00]."))
        with mock.patch("meetminer.guardrails.check_numbers", side_effect=RuntimeError("boom")):
            ans = qa.ask("q")
        self.assertEqual(ans.error, "")
        self.assertTrue(ans.text)
        self.assertEqual(ans.check.numbers, [])

    def test_a_wrong_number_in_a_real_call_is_flagged(self):
        text = f"The price is $30 per seat per month [{ONBOARDING}, Speaker 1, 00:00]."
        qa, _ = self.qa(reply(text))
        ans = qa.ask("What is the price?")
        flagged = ans.check.numbers_flagged
        self.assertEqual([(f.value, f.status) for f in flagged], [("$30", "elsewhere")])


# --------------------------------------------------------------------------- GR-COST-06


class ModelSwitchTests(Base):
    def compare(self, old, new, tokens=3854):
        return guardrails.compare_models(self.settings.price_table, old, new, "5m", tokens)

    def test_sonnet_costs_about_twenty_times_haiku(self):
        c = self.compare("claude-haiku-5-5", "claude-sonnet-5-5")
        self.assertTrue(c.more_expensive)
        self.assertAlmostEqual(c.multiple, 20.0, delta=0.5)
        self.assertGreater(c.new_cold, c.new_warm)
        self.assertGreater(c.old_cold, c.old_warm)

    def test_the_example_amounts_follow_the_size_of_the_transcripts(self):
        small = self.compare("claude-haiku-5-5", "claude-sonnet-5-5", 2_000)
        large = self.compare("claude-haiku-5-5", "claude-sonnet-5-5", 60_000)
        self.assertGreater(large.new_warm, small.new_warm * 3)  # the answer's own cost stays the same, so not 30x

    def test_going_cheaper_needs_no_confirmation(self):
        self.assertFalse(self.compare("claude-sonnet-5-5", "claude-haiku-5-5").more_expensive)

    def test_a_model_without_prices_gives_nothing_to_confirm(self):
        self.assertIsNone(self.compare("claude-haiku-5-5", "claude-unknown-9"))

    def test_the_confirmation_can_be_turned_off_in_settings(self):
        self.assertTrue(load_settings().confirm_model_switch)
        storage.update_user_settings(confirm_model_switch=False)
        self.assertFalse(load_settings().confirm_model_switch)


# --------------------------------------------------------------------------- GR-INT-04


class NumberCheckTests(Base):
    def check(self, answer: str):
        report = check_citations(answer, self.meetings)
        return guardrails.check_numbers(answer, self.meetings, report)

    def statuses(self, answer: str) -> list[tuple[str, str]]:
        return [(f.value, f.status) for f in self.check(answer)]

    def test_numbers_in_the_cited_turn_are_ok(self):
        found = self.statuses(f"The price is $25 per seat, which is $1,000 a month [{ONBOARDING}, Speaker 1, 00:00].")
        self.assertEqual(found, [("$25", "ok"), ("$1,000", "ok")])

    def test_a_number_from_another_turn_is_elsewhere_and_says_where(self):
        findings = self.check(f"The budget is $18,000 [{PLANNING}, Speaker 2, 01:35].")
        self.assertEqual(findings[0].status, "elsewhere")
        self.assertIn(f"{PLANNING}, Speaker 1, 00:00", findings[0].where)

    def test_a_number_in_no_transcript_is_missing(self):
        self.assertEqual(self.statuses(f"The budget is $19,500 [{PLANNING}, Speaker 1, 00:00]."), [("$19,500", "missing")])

    def test_amounts_may_be_written_in_several_ways(self):
        for written in ("$18,000", "18,000", "$18k", "18K", "18000"):
            with self.subTest(written=written):
                self.assertEqual(self.statuses(f"The budget is {written} [{PLANNING}, Speaker 1, 00:00]."),
                                 [(written, "ok")])

    def test_numbers_in_words_in_the_transcript_match_digits_in_the_answer(self):
        # "four days", "three days" and "three weeks" are spelled out in the sample
        self.assertEqual(self.statuses(f"He lost 4 days [{PLANNING}, Speaker 2, 01:35]."), [("4", "ok")])
        self.assertEqual(self.statuses(f"That gives 3 weeks of buffer [{PLANNING}, Speaker 1, 00:00]."), [("3", "ok")])

    def test_numbers_in_words_in_the_answer_are_compared_when_they_are_large(self):
        self.assertEqual(self.statuses(f"The budget is eighteen thousand dollars [{PLANNING}, Speaker 1, 00:00]."),
                         [("18000", "ok")])
        self.assertEqual(self.statuses(f"The budget is nineteen thousand dollars [{PLANNING}, Speaker 1, 00:00]."),
                         [("19000", "missing")])

    def test_small_counts_written_as_words_are_not_checked(self):
        self.assertEqual(self.statuses(f"There are two decisions and three risks [{PLANNING}, Speaker 1, 06:30]."), [])

    def test_ordinal_dates_match_digits(self):
        self.assertEqual(self.statuses(f"Launch is November 14th [{PLANNING}, Speaker 1, 00:00]."),
                         [("14th", "ok")])

    def test_a_wrong_day_is_flagged(self):
        self.assertEqual(self.statuses(f"Launch is November 15th [{PLANNING}, Speaker 1, 00:00]."),
                         [("15th", "missing")])

    def test_a_wrong_month_is_flagged_when_the_turn_names_other_months(self):
        findings = self.check(f"Launch is December 14 [{PLANNING}, Speaker 1, 00:00].")
        date = [f for f in findings if f.kind == "date"]
        self.assertEqual([(f.value, f.status) for f in date], [("December 14", "missing")])
        self.assertIn("November", date[0].where)

    def test_the_right_month_passes(self):
        self.assertEqual([f for f in self.check(f"Launch is November 14 [{PLANNING}, Speaker 1, 00:00].")
                          if f.kind == "date"], [])

    def test_percentages(self):
        self.assertEqual(self.statuses(f"There is a 10% discount [{ONBOARDING}, Speaker 1, 00:00]."), [("10%", "ok")])
        self.assertEqual(self.statuses(f"There is a 15% discount [{ONBOARDING}, Speaker 1, 00:00]."), [("15%", "missing")])

    def test_times_speaker_numbers_titles_and_list_markers_are_not_numbers(self):
        answer = (f"1. Speaker 3 said it at 03:05 [{PLANNING}, Speaker 3, 03:05].\n"
                  f"- Q3 is not mentioned [{PLANNING}, Speaker 3, 03:05].")
        self.assertEqual(self.statuses(answer), [])

    def test_a_number_before_a_citation_belongs_to_that_citation_not_the_next_one(self):
        answer = (f"The budget is $18,000 [{PLANNING}, Speaker 1, 00:00]. "
                  f"The price is $25 [{ONBOARDING}, Speaker 1, 00:00].")
        self.assertEqual(self.statuses(answer), [("$18,000", "ok"), ("$25", "ok")])

    def test_two_citations_in_a_row_share_their_numbers(self):
        answer = f"It is $18,000 and 25 customers [{PLANNING}, Speaker 1, 00:00], [{PLANNING}, Speaker 1, 06:30]."
        self.assertEqual({s for _, s in self.statuses(answer)}, {"ok"})

    def test_two_citations_joined_by_and_share_their_numbers(self):
        # real answers write "... the week of November 3rd [A] and [B]": the 3rd is in B, not in A
        answer = (f"He will do it the week of November 3rd [{PLANNING}, Speaker 1, 04:50] and "
                  f"[{PLANNING}, Speaker 2, 05:05].")
        self.assertEqual({s for _, s in self.statuses(answer)}, {"ok"})
        self.assertEqual(self.statuses(f"He will do it the week of November 3rd [{PLANNING}, Speaker 1, 04:50]."),
                         [("3rd", "elsewhere")])  # on its own, the first citation does not hold it

    def test_a_number_followed_by_a_colon_is_a_whole_number_but_a_time_is_not_a_number(self):
        answer = f'The budget was $12,000: "we originally had $12,000 for contractor help" [{PLANNING}, Speaker 1, 00:00].'
        self.assertEqual(self.statuses(answer), [("$12,000", "ok"), ("$12,000", "ok")])
        self.assertEqual([c for _, c in guardrails.extract_numbers("at 04:09 and 12:30:15, budget 5: 7")], ["5", "7"])

    def test_text_with_no_citation_is_only_looked_up_in_the_transcripts_as_a_whole(self):
        self.assertEqual(self.statuses("The budget is $18,000."), [("$18,000", "ok")])
        self.assertEqual(self.statuses("The budget is $77,000."), [("$77,000", "missing")])

    def test_a_not_found_answer_is_left_alone(self):
        self.assertEqual(self.statuses("Not found in the transcripts. I looked for the 2025 budget of $5,000."), [])

    def test_a_bad_citation_falls_back_to_the_whole_corpus(self):
        self.assertEqual(self.statuses("The budget is $18,000 [No such call, Speaker 1, 00:00]."), [("$18,000", "ok")])

    def test_describe_lists_each_problem_once(self):
        findings = self.check(f"It costs $19,500 [{PLANNING}, Speaker 1, 00:00] and again $19,500 [{PLANNING}, Speaker 1, 00:00].")
        lines = guardrails.describe_numbers(findings)
        self.assertEqual(len(lines), 1)
        self.assertIn("not found in the transcripts", lines[0])

    def test_report_property_lists_only_problems(self):
        answer = f"The budget is $18,000 and 31 beta customers [{PLANNING}, Speaker 1, 00:00]."
        report = check_citations(answer, self.meetings)
        report.numbers = guardrails.check_numbers(answer, self.meetings, report)
        self.assertEqual([f.value for f in report.numbers_flagged], ["31"])


class WordNumberTests(unittest.TestCase):
    def parse(self, text: str) -> list[str]:
        return [canon for _, canon in guardrails.extract_numbers(text, words_from=0)]

    def test_spelled_out_numbers(self):
        self.assertEqual(self.parse("twenty five people"), ["25"])
        self.assertEqual(self.parse("one hundred and five"), ["105"])
        self.assertEqual(self.parse("two thousand twenty six"), ["2026"])
        self.assertEqual(self.parse("one point"), ["1"])
        self.assertEqual(self.parse("a million dollars"), ["1000000"])
        self.assertEqual(self.parse("hundreds of thousands"), [])  # plurals are not numbers
        self.assertEqual(self.parse("1.5 million"), ["1500000"])

    def test_a_run_of_separate_numbers_stays_separate(self):
        self.assertEqual(self.parse("one two three"), ["1", "2", "3"])

    def test_ordinals(self):
        self.assertEqual(self.parse("the fourteenth and the twenty first"), ["14", "21"])


if __name__ == "__main__":
    unittest.main()
