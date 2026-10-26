"""Checks for the desktop window (no API key, no network, no cost):  python -m unittest discover -s tests

The window tests open a hidden Tk window; they are skipped on a machine with no display."""
from __future__ import annotations

import gc
import json
import os
import sys
import tempfile
import time
import tkinter as tk
import unittest
from datetime import datetime
from pathlib import Path
from tkinter import ttk
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))  # run from a checkout without installing

from meetminer import guardrails, storage, usagelog
from meetminer import app as ui
from meetminer.engine import Answer, load_settings
from meetminer.transcripts import check_citations, load_meetings

SAMPLE = """\
Speaker 1  00:00
Hello there. We can ship that change today. No blockers at all, and the price is $250k.

Speaker 2  00:49
a lot of notifications from the events.

Speaker 1  00:56
Our our meeting was quick. The budget is approved for the second quarter, that is fine.

Speaker 2  01:10
Okay thanks, that works for me.
"""


class HelperTests(unittest.TestCase):
    def test_block_kinds(self):
        self.assertEqual(ui.block_kind("## Payment terms")[:3], ("heading", "Payment terms", 2))
        self.assertEqual(ui.block_kind("- first point")[:2], ("bullet", "first point"))
        self.assertEqual(ui.block_kind("   - nested")[2], 1)
        self.assertEqual(ui.block_kind("3. third"), ("number", "third", 0, "3"))
        self.assertEqual(ui.block_kind("| a | b |")[0], "table")
        self.assertEqual(ui.block_kind("   ")[0], "blank")
        self.assertEqual(ui.block_kind("---")[0], "blank")
        self.assertEqual(ui.block_kind("Plain sentence.")[0], "para")

    def test_inline_runs(self):
        self.assertEqual(ui.inline_runs("a **b** `c` *d* e"),
                         [("a ", None), ("b", "bold"), (" ", None), ("c", "code"), (" ", None), ("d", "italic"),
                          (" e", None)])
        self.assertEqual(ui.inline_runs("2 * 3 * 4"), [("2 * 3 * 4", None)])  # not italics
        self.assertEqual(ui.inline_runs("no markup"), [("no markup", None)])

    def test_quote_spans_match_through_punctuation_and_stutters(self):
        text = "So our our conversation was, you know, quick. Then the price is $250k."
        (a, b), = ui.quote_spans(text, "our conversation was you know quick")
        self.assertEqual(text[a:b], "our our conversation was, you know, quick")

    def test_quote_spans_ellipsis_and_partial_word(self):
        text = "Yeah and it'll be an hour long too, so fine. We start Friday morning."
        spans = ui.quote_spans(text, "'ll be an hour long too ... start Friday morning")
        self.assertEqual([text[a:b] for a, b in spans], ["it'll be an hour long too", "start Friday morning"])

    def test_quote_spans_do_not_match_inside_words(self):
        self.assertEqual(ui.quote_spans("a small ball", "all ball"), [])
        self.assertEqual(ui.quote_spans("nothing here", "completely different words"), [])

    def test_unique_name_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            self.assertEqual(ui.unique_name(folder, "call.txt"), folder / "call.txt")
            (folder / "call.txt").write_text("x")
            (folder / "call (2).txt").write_text("x")
            self.assertEqual(ui.unique_name(folder, "call.txt"), folder / "call (3).txt")

    def test_folder_signature_changes_when_a_file_is_added(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = ui.folder_signature(Path(tmp))
            (Path(tmp) / "a.txt").write_text("hi")
            self.assertNotEqual(ui.folder_signature(Path(tmp)), before)
        self.assertEqual(ui.folder_signature(Path("Z:/no/such/folder")), ())

    def test_caption(self):
        ans = Answer(question="q", model="claude-haiku-5-5", cost=0.00042, seconds=2.5,
                     tokens={"input": 25, "cache_read": 15000, "cache_write": 0, "output": 500})
        text = ui.caption_for(ans)
        self.assertIn("$0.0004", text)
        self.assertIn("15,000", text)
        self.assertIn("claude-haiku-5-5", text)


class WindowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display available")
        self.root.withdraw()
        self.tmp = tempfile.TemporaryDirectory()
        self.home = tempfile.TemporaryDirectory()  # settings and logs of the "user" in these tests
        env = mock.patch.dict(os.environ, {"MEETMINER_HOME": self.home.name})
        env.start()
        self.addCleanup(env.stop)
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL"):
            os.environ.pop(var, None)
        (Path(self.tmp.name) / "Test call_transcript.txt").write_text(SAMPLE, encoding="utf-8")
        self.settings = load_settings()
        self.loaded = load_meetings(Path(self.tmp.name))
        patcher = mock.patch("meetminer.engine.MeetingQA.count_corpus_tokens", return_value=(1234, "measured"))
        patcher.start()
        self.addCleanup(patcher.stop)
        cred = mock.patch("meetminer.app.has_credentials", return_value=True)
        cred.start()
        self.addCleanup(cred.stop)
        self.app = ui.App(self.root, self.settings, self.loaded)

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()
        self.home.cleanup()
        # Tk objects of the closed window must be finalised here, on the main thread. Left for the garbage collector it
        # may run inside a later test's worker thread, where each one stalls that thread for about a second.
        self.app = self.root = None
        gc.collect()

    def pump(self, until, seconds=5.0):
        end = time.time() + seconds
        while time.time() < end and not until():
            self.root.update()
            time.sleep(0.01)
        self.root.update()

    def canned(self, question, history):
        text = ('**Budget:** the plan was approved for "second quarter" '
                '[Test call, Speaker 1, 00:56]. They also said "that works for me" [Test call, Speaker 1, 00:56].\n'
                "- Next step: a note without a quote [Test call, Speaker 2, 01:10]")
        ans = Answer(question=question, text=text, model="claude-haiku-5-5", cost=0.0005, seconds=1.2,
                     tokens={"input": 20, "cache_read": 15000, "cache_write": 0, "output": 300})
        ans.check = check_citations(text, self.loaded.meetings)
        self.app.tracker.record(question, ans.tokens, ans.cost, ans.seconds)
        return ans

    def test_sidebar_and_welcome(self):
        side = [w.cget("text") for w in self.app.meeting_box.winfo_children()[0].winfo_children()]
        self.assertEqual(side[0], "Test call")
        self.assertIn("4 turns", side[1])
        chat = self.app.chat.get("1.0", "end")
        self.assertIn("Ask about your meetings", chat)
        self.pump(lambda: "1,234" in self.app.tokens_label.cget("text"))
        self.assertIn("1,234 tokens", self.app.tokens_label.cget("text"))

    def test_ask_renders_answer_chips_and_cost(self):
        with mock.patch.object(self.app.qa, "ask", side_effect=self.canned):
            self.assertTrue(self.app.ask("What was agreed about payment?"))
            self.assertTrue(self.app.busy)
            self.assertFalse(self.app.ask("second question while busy"))  # ignored, not queued
            self.pump(lambda: not self.app.busy)
        self.assertFalse(self.app.busy)
        chat = self.app.chat.get("1.0", "end")
        self.assertIn("You", chat)
        self.assertIn("What was agreed about payment?", chat)
        self.assertIn("Budget:", chat)  # bold markers are gone
        self.assertNotIn("**", chat)
        self.assertIn("Test call  ·  Speaker 1  ·  00:56", chat)
        self.assertIn("•  Next step", chat)
        self.assertIn("Copy answer", chat)
        self.assertEqual(self.app.cost_label.cget("text"), "$0.0005")
        self.assertEqual(len(self.app.history), 1)
        self.assertTrue(self.app.chat.tag_ranges("cite_ok"))

    def test_clicking_a_citation_shows_the_highlighted_turn(self):
        ans = self.canned("q", [])
        self.app.show_citation(ans, 0)
        viewer = self.app.viewer
        self.assertEqual(self.app.viewer_title.cget("text"), "Test call")
        self.assertIn("The quoted words are in the highlighted turn", self.app.viewer_note.cget("text"))
        focus = viewer.get(*viewer.tag_ranges("focus")[:2])
        self.assertIn("The budget is approved", focus)
        highlighted = "".join(viewer.get(str(a), str(b)) for a, b in zip(viewer.tag_ranges("quote")[::2],
                                                                           viewer.tag_ranges("quote")[1::2]))
        self.assertIn("second quarter", highlighted)

    def test_wrong_turn_citation_is_centred_on_the_real_turn(self):
        text = '"The budget is approved for the second quarter" [Test call, Speaker 2, 01:10]'
        ans = Answer(question="q", text=text)
        ans.check = check_citations(text, self.loaded.meetings)
        self.assertEqual(ans.check.citations[0].status, "weak")
        self.app.show_citation(ans, 0)
        viewer = self.app.viewer
        self.assertIn("not at 01:10", self.app.viewer_note.cget("text"))
        self.assertIn("The budget is approved", viewer.get(*viewer.tag_ranges("focus")[:2]))

    def test_meeting_click_and_new_chat(self):
        self.app.show_meeting(self.loaded.meetings[0])
        self.assertIn("a lot of notifications", self.app.viewer.get("1.0", "end"))
        with mock.patch.object(self.app.qa, "ask", side_effect=self.canned):
            self.app.ask("q")
            self.pump(lambda: not self.app.busy)
        self.app.new_chat()
        self.assertEqual(self.app.history, [])
        self.assertIn("Ask about your meetings", self.app.chat.get("1.0", "end"))

    def test_error_answers_are_shown_and_do_not_join_the_history_sent_to_the_model(self):
        failed = Answer(question="q", error="The API key was rejected.")
        with mock.patch.object(self.app.qa, "ask", return_value=failed):
            self.app.ask("q")
            self.pump(lambda: not self.app.busy)
        self.assertIn("The API key was rejected.", self.app.chat.get("1.0", "end"))
        seen = []
        with mock.patch.object(self.app.qa, "ask", side_effect=lambda q, h: (seen.append(h), self.canned(q, h))[1]):
            self.app.ask("next")
            self.pump(lambda: not self.app.busy)
        self.assertEqual(seen, [[]])  # the failed turn was not sent back as history

    def test_model_switch_keeps_the_running_total(self):
        with mock.patch.object(self.app.qa, "ask", side_effect=self.canned):
            self.app.ask("q")
            self.pump(lambda: not self.app.busy)
        total = self.app.tracker.total
        self.app.model_var.set(ui.MODEL_LABELS["claude-sonnet-5-5"])
        with mock.patch("meetminer.app.messagebox.askyesno", return_value=True):
            self.app.on_model()
        self.assertEqual(self.app.settings.model, "claude-sonnet-5-5")
        self.assertEqual(self.app.qa.settings.model, "claude-sonnet-5-5")
        self.assertEqual(self.app.tracker.total, total)
        self.assertEqual(self.app.tracker.settings.model, "claude-sonnet-5-5")

    # ------------------------------------------------------------------ GR-COST-06: asking before a pricier model

    def switch_to(self, model: str):
        self.app.model_var.set(ui.MODEL_LABELS[model])
        self.app.on_model()

    def test_a_more_expensive_model_asks_first_with_the_cost_multiple(self):
        self.pump(lambda: self.app.prompt_tokens[0] == 1234)  # the token count arrives from a background thread
        with mock.patch("meetminer.app.messagebox.askyesno", return_value=False) as ask:
            self.switch_to("claude-sonnet-5-5")
        ask.assert_called_once()
        message = ask.call_args.args[1]
        self.assertIn("Sonnet 5.5 costs about 20× more per question than Haiku 5.5", message)
        self.assertIn("1,234 tokens", message)
        self.assertEqual(ask.call_args.kwargs["default"], "no")  # Enter must not approve a cost increase

    def test_saying_no_keeps_the_model_and_puts_the_box_back(self):
        with mock.patch("meetminer.app.messagebox.askyesno", return_value=False):
            self.switch_to("claude-sonnet-5-5")
        self.assertEqual(self.app.settings.model, "claude-haiku-5-5")
        self.assertEqual(self.app.qa.settings.model, "claude-haiku-5-5")
        self.assertEqual(self.app.model_var.get(), ui.MODEL_LABELS["claude-haiku-5-5"])

    def test_a_cheaper_model_needs_no_confirmation(self):
        with mock.patch("meetminer.app.messagebox.askyesno", return_value=True):
            self.switch_to("claude-sonnet-5-5")
        with mock.patch("meetminer.app.messagebox.askyesno") as ask:
            self.switch_to("claude-haiku-5-5")
        ask.assert_not_called()
        self.assertEqual(self.app.settings.model, "claude-haiku-5-5")

    def test_the_confirmation_can_be_switched_off(self):
        self.app.set_confirm_model_switch(False)
        with mock.patch("meetminer.app.messagebox.askyesno") as ask:
            self.switch_to("claude-sonnet-5-5")
        ask.assert_not_called()
        self.assertEqual(self.app.settings.model, "claude-sonnet-5-5")
        self.assertFalse(storage.load_user_settings()["confirm_model_switch"])

    # ------------------------------------------------------------------ GR-COST-05 and GR-INT-04 in the window

    def notice_texts(self) -> list[str]:
        return [w.cget("text") for row in self.app.notices.winfo_children() for w in row.winfo_children()]

    def log_spend(self, dollars: float) -> None:
        usagelog.append(self.settings.usage_log, {
            "time": datetime.now().isoformat(timespec="seconds"), "model": "claude-haiku-5-5", "effort": "low",
            "input": 1, "cache_write": 0, "cache_read": 0, "output": 1, "cost_usd": f"{dollars:.6f}", "seconds": "1.0",
            "question": "earlier", "status": "ok", "error_kind": ""})

    def test_a_daily_limit_that_is_reached_pauses_questions_and_says_so(self):
        self.app.set_period_limit("max_daily_cost_usd", 1.0)
        self.log_spend(1.5)
        self.app.refresh_notices()
        self.assertTrue(any("Daily spending limit reached" in t for t in self.notice_texts()))
        self.assertEqual(str(self.app.ask_button.cget("state")), "disabled")
        with mock.patch.object(self.app.qa, "ask") as ask:
            self.assertFalse(self.app.ask("one more?"))
            ask.assert_not_called()
        self.assertEqual(storage.load_user_settings()["max_daily_cost_usd"], 1.0)

    def test_nearing_a_limit_shows_a_warning_but_still_answers(self):
        self.app.set_period_limit("max_daily_cost_usd", 0)  # the default daily limit would be reached first
        self.app.set_period_limit("max_monthly_cost_usd", 10.0)
        self.log_spend(8.5)
        self.app.refresh_notices()
        self.assertTrue(any("85% of the monthly spending limit" in t for t in self.notice_texts()))
        self.assertFalse(self.app.spend_blocked)

    def test_raising_the_limit_lifts_the_pause(self):
        self.app.set_period_limit("max_daily_cost_usd", 1.0)
        self.log_spend(1.5)
        self.app.refresh_notices()
        self.assertTrue(self.app.spend_blocked)
        self.app.set_period_limit("max_daily_cost_usd", 0)  # 0 = no limit
        self.assertFalse(self.app.spend_blocked)
        self.assertEqual(str(self.app.ask_button.cget("state")), "normal")

    def test_flagged_numbers_are_listed_under_the_answer(self):
        def answer(question, history):
            text = f'The budget is $19,500 [Test call, Speaker 1, 00:56]. It starts in 2 days.'
            ans = Answer(question=question, text=text, model="claude-haiku-5-5", cost=0.0005, seconds=1.0,
                         tokens={"input": 20, "cache_read": 1500, "cache_write": 0, "output": 30})
            ans.check = check_citations(text, self.loaded.meetings)
            ans.check.numbers = guardrails.check_numbers(text, self.loaded.meetings, ans.check)
            return ans

        with mock.patch.object(self.app.qa, "ask", side_effect=answer):
            self.app.ask("What is the budget?")
            self.pump(lambda: not self.app.busy)
        chat = self.app.chat.get("1.0", "end")
        self.assertIn("Check these numbers against the transcript:", chat)
        self.assertIn("$19,500: not found in the transcripts", chat)

    # ------------------------------------------------------------------ first run and settings

    def fresh_app(self, with_key: bool, with_transcripts: bool):
        """An app as a new user sees it: a transcripts folder that may be empty, and maybe no key."""
        self.app.close()
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir(exist_ok=True)
        folder = Path(self.tmp.name) if with_transcripts else empty
        settings = __import__("dataclasses").replace(self.settings, transcripts_dir=folder)
        root = tk.Tk()
        root.withdraw()
        self.root = root
        with mock.patch("meetminer.app.has_credentials", return_value=with_key):
            self.app = ui.App(root, settings, load_meetings(folder))
        self.key_patch = mock.patch("meetminer.app.has_credentials", return_value=with_key)
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)
        return self.app

    def labels_in_chat(self) -> list[str]:
        """Every label and button text inside the windows embedded in the chat (the setup cards)."""
        found: list[str] = []

        def walk(widget):
            for child in widget.winfo_children():
                if "text" in child.keys() and child.cget("text"):
                    found.append(str(child.cget("text")))
                walk(child)

        for name in self.app.chat.window_names():
            walk(self.root.nametowidget(name))
        return found

    def test_a_new_user_sees_the_two_setup_steps(self):
        app = self.fresh_app(with_key=False, with_transcripts=False)
        self.assertTrue(app.setup_visible)
        self.assertIn("Welcome to MeetMiner", app.chat.get("1.0", "end"))
        labels = " | ".join(self.labels_in_chat())
        self.assertIn("Add your Anthropic API key", labels)
        self.assertIn("Add your transcripts", labels)
        self.assertIn(str(app.settings.transcripts_dir), labels)  # tells them which folder to use
        for button in ("Open Settings", "Get a key", "Add files…", "Open folder"):
            self.assertIn(button, labels)
        self.assertNotIn("Ask about your meetings", app.chat.get("1.0", "end"))
        # the checklist already says it, so the top bar does not repeat it
        self.assertEqual(app.notices.winfo_children(), [])

    def test_step_one_ticks_when_the_key_exists_and_step_two_is_still_open(self):
        app = self.fresh_app(with_key=True, with_transcripts=False)
        self.assertTrue(app.setup_visible)
        labels = self.labels_in_chat()
        self.assertIn("API key saved.", labels)
        self.assertIn("Change key", labels)
        self.assertTrue(any(text.startswith("In Otter, open a conversation") for text in labels))

    def test_asking_without_transcripts_shows_the_steps_instead_of_failing(self):
        app = self.fresh_app(with_key=True, with_transcripts=False)
        with mock.patch.object(app.qa, "ask") as ask:
            self.assertFalse(app.ask("anything?"))
            ask.assert_not_called()
        self.assertTrue(app.setup_visible)

    def test_adding_files_copies_them_and_the_setup_screen_turns_into_the_welcome_screen(self):
        app = self.fresh_app(with_key=True, with_transcripts=False)
        folder = app.settings.transcripts_dir
        source = Path(self.tmp.name) / "Test call_transcript.txt"
        self.assertEqual(app.add_files([str(source)]), 1)
        self.assertTrue((folder / source.name).is_file())
        self.assertEqual(len(app.loaded.meetings), 1)
        self.assertFalse(app.setup_visible)
        self.assertIn("Ask about your meetings", app.chat.get("1.0", "end"))
        self.assertEqual(app.add_files([str(source)]), 1)  # the same file again is kept as a second copy
        self.assertTrue((folder / "Test call_transcript (2).txt").is_file())

    def test_files_dropped_into_the_folder_are_noticed_when_the_window_is_used_again(self):
        app = self.fresh_app(with_key=True, with_transcripts=False)
        (app.settings.transcripts_dir / "dropped_transcript.txt").write_text(SAMPLE, encoding="utf-8")
        event = mock.Mock(widget=self.root)
        app._on_focus(event)
        self.assertEqual(len(app.loaded.meetings), 1)

    def test_settings_window_saves_the_key_encrypted_and_the_app_notices(self):
        app = self.fresh_app(with_key=False, with_transcripts=True)
        dialog = ui.SettingsDialog(app, "key")
        try:
            self.assertIn("No key yet", dialog.key_status.cget("text"))
            dialog.key_var.set("sk-ant-api03-FAKE-for-the-settings-test-0123456789")
            with mock.patch.object(app, "on_key_changed") as changed:
                dialog.save_key()
                changed.assert_called_once()
            raw = storage.settings_path().read_text(encoding="utf-8")
            self.assertNotIn("FAKE", raw)
            self.assertEqual(storage.get_api_key(), "sk-ant-api03-FAKE-for-the-settings-test-0123456789")
            self.assertIn("A key is saved", dialog.key_status.cget("text"))
            self.assertEqual(dialog.key_var.get(), "")  # the typed key is cleared from the box
            with mock.patch("meetminer.app.messagebox.askyesno", return_value=True):
                dialog.remove_key()
            self.assertIsNone(storage.get_api_key())
        finally:
            dialog.destroy()

    def test_settings_window_rejects_an_empty_key_and_a_bad_limit(self):
        app = self.fresh_app(with_key=True, with_transcripts=True)
        dialog = ui.SettingsDialog(app)
        try:
            dialog.save_key()
            self.assertIn("Paste your key", dialog.key_message.cget("text"))
            dialog.limit_var.set("lots")
            with mock.patch("meetminer.app.messagebox.showinfo") as info:
                dialog.save_limit()
                info.assert_called_once()
            self.assertEqual(app.settings.max_session_cost_usd, self.settings.max_session_cost_usd)
        finally:
            dialog.destroy()

    def test_spending_limit_is_saved_and_enforced(self):
        app = self.fresh_app(with_key=True, with_transcripts=True)
        app.set_spending_limit(0.01)
        self.assertEqual(storage.load_user_settings()["max_session_cost_usd"], 0.01)
        self.assertEqual(app.qa.settings.max_session_cost_usd, 0.01)
        app.tracker.record("q", {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}, 0.02, 1.0)
        app.refresh_notices()
        texts = [w.cget("text") for row in app.notices.winfo_children() for w in row.winfo_children()]
        self.assertTrue(any("spending limit reached" in t for t in texts))
        self.assertFalse(app.ask("one more?"))

    def test_choosing_another_transcripts_folder_is_saved_and_loaded(self):
        app = self.fresh_app(with_key=True, with_transcripts=False)
        other = Path(self.tmp.name) / "other"
        other.mkdir()
        (other / "x_transcript.txt").write_text(SAMPLE, encoding="utf-8")
        app.set_transcripts_folder(other)
        self.assertEqual(storage.load_user_settings()["transcripts_dir"], str(other))
        self.assertEqual(len(app.loaded.meetings), 1)
        self.assertEqual(app.settings.transcripts_dir, other)

    def test_key_test_reports_rejection_without_a_network(self):
        app = self.fresh_app(with_key=True, with_transcripts=True)
        dialog = ui.SettingsDialog(app)
        try:
            import anthropic
            error = anthropic.AuthenticationError("bad", response=mock.Mock(status_code=401, headers={}), body=None)
            with mock.patch("anthropic.resources.messages.Messages.count_tokens", side_effect=error):
                dialog.key_var.set("sk-ant-wrong")
                dialog.test_key()
                self.pump_dialog(dialog, lambda: "rejected" in dialog.key_message.cget("text"))
            self.assertIn("rejected", dialog.key_message.cget("text"))
        finally:
            dialog.destroy()

    def pump_dialog(self, dialog, until, seconds=5.0):
        end = time.time() + seconds
        while time.time() < end and not until():
            dialog.update()
            time.sleep(0.01)

    def test_no_key_shows_a_notice_and_asks_for_one(self):
        with mock.patch("meetminer.app.has_credentials", return_value=False):
            self.app.refresh_notices()
            labels = [w.cget("text") for row in self.app.notices.winfo_children() for w in row.winfo_children()]
            self.assertTrue(any("No API key" in t for t in labels))
            with mock.patch.object(self.app, "ask_for_key") as dialog:
                self.assertFalse(self.app.ask("hello"))
                dialog.assert_called_once()

    # ------------------------------------------------------------------ the Guardrails and Reports pages

    def test_settings_has_four_pages_and_opens_on_the_one_asked_for(self):
        app = self.fresh_app(with_key=True, with_transcripts=True)
        for focus, index in (("reports", 2), ("rag-reports", 3), ("guardrails", 1), (None, 0)):
            dialog = ui.SettingsDialog(app, focus)
            try:
                self.assertEqual([dialog.tabs.tab(t, "text").strip() for t in dialog.tabs.tabs()],
                                 ["General", "Guardrails", "Full-context reports", "RAG reports"])
                self.assertEqual(dialog.tabs.index(dialog.tabs.select()), index, focus)
            finally:
                dialog.destroy()

    # ------------------------------------------------------------------ the engine switch and the RAG reports page

    def test_the_engine_switch_changes_the_engine_everywhere_and_is_remembered(self):
        self.assertEqual(self.app.settings.engine, "full")
        self.assertEqual(self.app.engine_buttons["full"].cget("bg"), ui.C["accent"])
        self.app.set_engine("rag")
        for s in (self.app.settings, self.app.base_settings, self.app.qa.settings, self.app.tracker.settings):
            self.assertEqual(s.engine, "rag")
        self.assertEqual(storage.load_user_settings()["engine"], "rag")
        self.assertEqual(self.app.engine_buttons["rag"].cget("bg"), ui.C["accent"])
        self.assertIn("only the passages", self.app.engine_note.cget("text"))
        with mock.patch("meetminer.app.messagebox.askyesno", return_value=True):  # a model switch keeps the engine
            self.switch_to("claude-sonnet-5-5")
        self.assertEqual(self.app.settings.engine, "rag")
        self.assertEqual(load_settings().engine, "rag")  # a new start uses it too

    def test_the_engine_cannot_change_while_an_answer_is_on_its_way(self):
        self.app.busy = True
        self.app.set_engine("rag")
        self.assertEqual(self.app.settings.engine, "full")
        self.app.busy = False

    def test_a_rag_answer_is_asked_with_rag_and_says_so_under_the_answer(self):
        self.app.set_engine("rag")
        client = mock.Mock()
        client.messages.create.return_value = mock.Mock(
            content=[mock.Mock(type="text", text="The budget is approved [Test call, Speaker 1, 00:56].")],
            stop_reason="end_turn", usage=mock.Mock(input_tokens=900, cache_creation_input_tokens=0,
                                                    cache_read_input_tokens=0, output_tokens=40))
        self.app.qa._client = client
        self.assertTrue(self.app.ask("Was the budget approved?"))
        self.pump(lambda: not self.app.busy)
        system = client.messages.create.call_args.kwargs["system"][0]
        self.assertNotIn("cache_control", system)
        self.assertIn("excerpts", system["text"])
        chat = self.app.chat.get("1.0", "end")
        self.assertIn("RAG:", chat)
        self.assertIn("passages from 1 meeting", chat)

    def test_a_rag_question_that_matches_nothing_costs_nothing(self):
        self.app.set_engine("rag")
        client = mock.Mock()
        self.app.qa._client = client
        self.assertTrue(self.app.ask("Was Kubernetes discussed?"))
        self.pump(lambda: not self.app.busy)
        client.messages.create.assert_not_called()
        chat = self.app.chat.get("1.0", "end")
        self.assertIn("nothing was sent to Claude", chat)
        self.assertIn("no passage matched, nothing was sent", chat)

    def test_the_large_transcripts_notice_offers_rag_and_disappears_with_it(self):
        self.app.prompt_tokens = (70_000, "measured")
        self.app.refresh_notices()
        buttons = [w for row in self.app.notices.winfo_children() for w in row.winfo_children()
                   if isinstance(w, tk.Button)]
        self.assertIn("Switch to RAG", [b.cget("text") for b in buttons])
        next(b for b in buttons if b.cget("text") == "Switch to RAG").invoke()
        self.assertEqual(self.app.settings.engine, "rag")
        self.assertFalse(any("above the" in t for t in self.notice_texts()))

    def test_each_reports_page_shows_only_its_own_engine(self):
        app = self.fresh_app(with_key=True, with_transcripts=True)
        for engine, n, seconds in (("full", 2, 2.0), ("rag", 3, 1.0)):
            for _ in range(n):
                usagelog.append(app.settings.usage_log, {
                    "time": datetime.now().isoformat(timespec="seconds"), "model": "claude-haiku-5-5", "effort": "low",
                    "input": 10, "cache_write": 0, "cache_read": 0, "output": 100, "cost_usd": "0.000500",
                    "seconds": str(seconds), "question": "q", "status": "ok", "error_kind": "", "engine": engine})
        usagelog.append(app.settings.usage_log, {
            "time": datetime.now().isoformat(timespec="seconds"), "model": "claude-haiku-5-5", "effort": "low",
            "input": 0, "cache_write": 0, "cache_read": 0, "output": 0, "cost_usd": "0", "seconds": "0", "question": "q",
            "status": "ok", "error_kind": "no_match", "engine": "rag"})
        self.write_scorecard(storage.data_dir() / "reports" / "scorecard-rag.json")
        dialog = ui.SettingsDialog(app, "rag-reports")
        try:
            full, rag = self.panel_text(dialog.reports_panel), self.panel_text(dialog.rag_reports_panel)
            self.assertIn("2 answers", full)
            self.assertIn("answered with Full context", full)
            self.assertIn("3 answers", rag)
            self.assertIn("answered with RAG", rag)
            self.assertIn("1 question matched no passage", rag)
            self.assertIn("Release scorecard: RAG", rag)
            self.assertIn("MeetMiner 9.9.9", rag)
            self.assertNotIn("matched no passage", full)
        finally:
            dialog.destroy()

    def test_daily_and_monthly_limits_are_validated_and_saved(self):
        app = self.fresh_app(with_key=True, with_transcripts=True)
        dialog = ui.SettingsDialog(app, "guardrails")
        try:
            self.assertEqual(dialog.daily_var.get(), "5.00")
            dialog.daily_var.set("2.5")
            dialog.save_period("max_daily_cost_usd", dialog.daily_var)
            self.assertEqual(app.settings.max_daily_cost_usd, 2.5)
            self.assertEqual(storage.load_user_settings()["max_daily_cost_usd"], 2.5)
            self.assertEqual(dialog.daily_var.get(), "2.50")
            dialog.monthly_var.set("0")  # allowed: no monthly limit
            dialog.save_period("max_monthly_cost_usd", dialog.monthly_var)
            self.assertEqual(storage.load_user_settings()["max_monthly_cost_usd"], 0.0)
            dialog.monthly_var.set("-3")
            with mock.patch("meetminer.app.messagebox.showinfo") as info:
                dialog.save_period("max_monthly_cost_usd", dialog.monthly_var)
                info.assert_called_once()
            self.assertEqual(app.settings.max_monthly_cost_usd, 0.0)
        finally:
            dialog.destroy()

    def test_the_confirmation_checkbox_is_saved(self):
        app = self.fresh_app(with_key=True, with_transcripts=True)
        dialog = ui.SettingsDialog(app, "guardrails")
        try:
            self.assertTrue(dialog.confirm_var.get())
            dialog.confirm_var.set(False)
            dialog.save_confirm()
            self.assertFalse(app.settings.confirm_model_switch)
        finally:
            dialog.destroy()

    def panel_text(self, panel) -> str:
        found: list[str] = []

        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child, ttk.Treeview):
                    found.extend(" | ".join(str(v) for v in child.item(i, "values")) for i in child.get_children())
                elif "text" in child.keys() and child.cget("text"):
                    found.append(str(child.cget("text")))
                walk(child)

        walk(panel.body)
        return "\n".join(found)

    def write_scorecard(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema": 1, "app": "MeetMiner", "version": "9.9.9", "generated": "2026-10-25T12:00:00", "decision": "REVIEW",
            "total": 88.0, "gates_failed": [], "unmeasured": ["p95 latency, cold"],
            "dimensions": [{"id": "ops", "name": "Operational performance", "weight": 20, "score": 75.0, "metrics": [
                {"id": "p95", "name": "p95 latency, warm", "target": "<= 8 s", "measured": "3.1 s", "rating": "green", "gate": False},
                {"id": "fail", "name": "Failure rate", "target": "<= 1%", "measured": "2.0%", "rating": "amber", "gate": True}]}],
            "notes": ["Measured on the sample transcripts."]}), encoding="utf-8")

    def test_reports_page_shows_spending_usage_and_the_scorecard(self):
        app = self.fresh_app(with_key=True, with_transcripts=True)
        for seconds, cost in ((1.0, 0.001), (2.0, 0.002), (4.0, 0.003)):
            usagelog.append(app.settings.usage_log, {
                "time": datetime.now().isoformat(timespec="seconds"), "model": "claude-haiku-5-5", "effort": "low",
                "input": 10, "cache_write": 0, "cache_read": 1000, "output": 100, "cost_usd": f"{cost:.6f}",
                "seconds": str(seconds), "question": "q", "status": "ok", "error_kind": ""})
        app.tracker.record_failure("q", "timeout", 60.0)
        self.write_scorecard(storage.data_dir() / "reports" / "scorecard.json")
        dialog = ui.SettingsDialog(app, "reports")
        try:
            text = self.panel_text(dialog.reports_panel)
            for expected in ("This month", "3 answers", "median 2.0 s", "slowest 5% 4.0 s", "1 attempt gave no answer",
                             "claude-haiku-5-5 | 3", "MeetMiner 9.9.9 · REVIEW · 88/100", "Operational performance",
                             "p95 latency, warm | <= 8 s | 3.1 s | good", "Failure rate [gate] | <= 1% | 2.0% | watch",
                             "Not measured yet: p95 latency, cold"):
                self.assertIn(expected, text)
        finally:
            dialog.destroy()

    def test_reports_page_without_data_says_so(self):
        app = self.fresh_app(with_key=True, with_transcripts=True)
        with mock.patch("meetminer.reports.scorecard_candidates", return_value=[]):
            dialog = ui.SettingsDialog(app, "reports")
            try:
                text = self.panel_text(dialog.reports_panel)
                self.assertIn("Nothing yet", text)
                self.assertIn("No scorecard is available", text)
            finally:
                dialog.destroy()

    def test_opening_a_scorecard_file_replaces_the_shown_one(self):
        app = self.fresh_app(with_key=True, with_transcripts=True)
        chosen = Path(self.tmp.name) / "other-scorecard.json"
        self.write_scorecard(chosen)
        with mock.patch("meetminer.reports.scorecard_candidates", return_value=[]):
            dialog = ui.SettingsDialog(app, "reports")
            try:
                with mock.patch("meetminer.reports_ui.filedialog.askopenfilename", return_value=str(chosen)):
                    dialog.reports_panel.open_file()
                self.assertIn("MeetMiner 9.9.9", self.panel_text(dialog.reports_panel))
                bad = Path(self.tmp.name) / "bad.json"
                bad.write_text("{}", encoding="utf-8")
                with mock.patch("meetminer.reports_ui.filedialog.askopenfilename", return_value=str(bad)), \
                        mock.patch("meetminer.reports_ui.messagebox.showinfo") as info:
                    dialog.reports_panel.open_file()
                    info.assert_called_once()
                self.assertIn("MeetMiner 9.9.9", self.panel_text(dialog.reports_panel))  # the good one stays
            finally:
                dialog.destroy()


if __name__ == "__main__":
    unittest.main()
