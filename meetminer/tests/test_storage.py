"""Checks for per-user storage and the saved API key (no network, no real key):  python -m unittest discover -s tests"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))  # run from a checkout without installing

from meetminer import storage
from meetminer import engine

FAKE_KEY = "sk-ant-api03-THIS-IS-A-FAKE-KEY-FOR-TESTS-0123456789"
SHIPPED_CONFIG = Path(__file__).resolve().parent.parent / "src" / "meetminer" / "config" / "default.yaml"


class AppDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {"MEETMINER_HOME": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL"):
            os.environ.pop(var, None)
        self.addCleanup(self.tmp.cleanup)

    def test_key_is_encrypted_on_disk_and_round_trips(self):
        storage.set_api_key(FAKE_KEY)
        text = storage.settings_path().read_text(encoding="utf-8")
        self.assertNotIn("FAKE-KEY", text)
        self.assertNotIn("sk-ant", text)
        self.assertTrue(text.split('"api_key": "')[1].startswith("dpapi:"))
        self.assertEqual(storage.get_api_key(), FAKE_KEY)

    def test_removing_the_key(self):
        storage.set_api_key(FAKE_KEY)
        storage.set_api_key("  ")
        self.assertIsNone(storage.get_api_key())
        self.assertNotIn("api_key", storage.load_user_settings())

    def test_a_key_copied_from_another_user_or_pc_is_not_readable(self):
        storage.update_user_settings(api_key="dpapi:" + "AAAA" * 20)  # not something this account encrypted
        self.assertIsNone(storage.get_api_key())

    def test_other_settings_survive_a_key_change(self):
        storage.update_user_settings(model="claude-sonnet-5-5")
        storage.set_api_key(FAKE_KEY)
        storage.set_api_key(None)
        self.assertEqual(storage.load_user_settings(), {"model": "claude-sonnet-5-5"})

    def test_settings_file_with_a_byte_order_mark_is_still_read(self):
        storage.settings_path().write_text('{"model": "claude-sonnet-5-5"}', encoding="utf-8-sig")
        self.assertEqual(storage.load_user_settings(), {"model": "claude-sonnet-5-5"})

    def test_key_hint_never_shows_the_whole_key(self):
        hint = storage.key_hint(FAKE_KEY)
        self.assertLess(len(hint), len(FAKE_KEY) // 2)
        self.assertTrue(hint.startswith("sk-ant-"))
        self.assertEqual(storage.key_hint("short"), "saved")

    def test_has_credentials_sees_the_saved_key(self):
        self.assertFalse(engine.has_credentials())
        storage.set_api_key(FAKE_KEY)
        self.assertTrue(engine.has_credentials())

    def test_client_uses_the_saved_key(self):
        storage.set_api_key(FAKE_KEY)
        qa = engine.MeetingQA(engine.load_settings(), [])
        self.assertEqual(qa.client.api_key, FAKE_KEY)
        storage.set_api_key(None)
        qa.reset_client()
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-from-env"}):
            self.assertEqual(engine.MeetingQA(engine.load_settings(), []).client.api_key, "sk-from-env")

    def test_defaults_when_nothing_is_set(self):
        s = engine.load_settings(SHIPPED_CONFIG)  # the config the installed app ships with
        self.assertEqual(s.transcripts_dir, storage.default_transcripts_dir())
        self.assertEqual(s.transcripts_dir.parts[-2:], ("MeetMiner", "Transcripts"))
        home = Path(self.tmp.name).resolve()  # resolve: Windows may show the same folder with a short name (ADMINI~1)
        self.assertEqual(s.usage_log.parent.resolve(), home)
        self.assertEqual(s.base_dir.resolve(), home)

    def test_user_choices_win_over_the_shipped_config(self):
        elsewhere = Path(self.tmp.name) / "my transcripts"
        storage.update_user_settings(transcripts_dir=str(elsewhere), model="claude-sonnet-5-5", max_session_cost_usd=7.5)
        s = engine.load_settings()
        self.assertEqual(s.transcripts_dir, elsewhere)
        self.assertEqual(s.model, "claude-sonnet-5-5")
        self.assertEqual(s.max_session_cost_usd, 7.5)
        with mock.patch.dict(os.environ, {"ANTHROPIC_MODEL": "claude-opus-5-5"}):  # the environment still wins
            self.assertEqual(engine.load_settings().model, "claude-opus-5-5")

    def test_corrupt_settings_file_is_ignored(self):
        storage.settings_path().write_text("{ this is not json", encoding="utf-8")
        self.assertEqual(storage.load_user_settings(), {})
        self.assertIsNone(storage.get_api_key())

    def test_shipped_default_config_is_generic(self):
        text = SHIPPED_CONFIG.read_text(encoding="utf-8")
        self.assertIn('transcripts_dir: ""', text)
        for needle in ("D:\\", "C:\\", "Users\\"):  # no machine-specific path may ship in the default config
            self.assertNotIn(needle, text)
        s = engine.load_settings(SHIPPED_CONFIG)
        self.assertEqual(s.model, "claude-haiku-5-5")
        self.assertEqual(len(s.examples), 3)


if __name__ == "__main__":
    unittest.main()
