"""Checks for the setup wizard (packaging/installer.py) with a fake app payload. Shortcuts and the registry entry go to a
temporary sandbox, never to the real Start menu, Desktop or Installed-apps list.   python -m unittest discover -s tests"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import tempfile
import time
import tkinter as tk
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))  # run from a checkout without installing

from meetminer import winsetup

spec = importlib.util.spec_from_file_location("installer", ROOT / "packaging" / "installer.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def args_for(sandbox: Path, **extra) -> argparse.Namespace:
    base = dict(silent=False, dir=str(sandbox / "Program Files" / "MeetMiner"), no_desktop=False, no_launch=True,
                payload=None, sandbox=str(sandbox))
    return argparse.Namespace(**{**base, **extra})


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root_dir = Path(self.tmp.name)
        self.payload = self.root_dir / "payload.zip"
        with zipfile.ZipFile(self.payload, "w") as z:
            z.writestr("MeetMiner.exe", b"MZ-fake")
            z.writestr("_internal/base.dll", b"dll")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: winsetup.unregister_uninstall(winsetup.make_layout(sandbox=self.root_dir)))

    def test_silent_install_succeeds_and_reports_failures_by_exit_code(self):
        args = args_for(self.root_dir, silent=True)
        self.assertEqual(installer.run_silent(args, self.payload), 0)
        layout = winsetup.make_layout(sandbox=self.root_dir)
        self.assertTrue(layout.exe.is_file())
        self.assertTrue(layout.desktop_link.is_file())
        with open(layout.exe, "rb"):  # the app is "open": setup must say so with its own exit code
            self.assertEqual(installer.run_silent(args, self.payload), 3)

    def test_silent_options(self):
        self.assertEqual(installer.run_silent(args_for(self.root_dir, no_desktop=True), self.payload), 0)
        self.assertFalse(winsetup.make_layout(sandbox=self.root_dir).desktop_link.exists())

    def test_slash_s_means_silent(self):
        with mock.patch.object(installer, "run_silent", return_value=0) as run, \
                mock.patch.object(installer, "find_payload", return_value=self.payload):
            self.assertEqual(installer.main(["/S", "--no-launch"]), 0)
            self.assertTrue(run.call_args.args[0].silent)

    def test_missing_payload_is_reported_not_crashed(self):
        with mock.patch.object(installer, "find_payload", return_value=self.root_dir / "nope.zip"):
            self.assertEqual(installer.main(["--silent"]), 2)


class WizardTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display available")
        self.root.withdraw()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.payload = self.dir / "payload.zip"
        with zipfile.ZipFile(self.payload, "w") as z:
            z.writestr("MeetMiner.exe", b"MZ-fake")
            z.writestr("_internal/base.dll", b"dll")
        self.wizard = installer.Wizard(self.root, self.payload, args_for(self.dir))
        self.addCleanup(lambda: winsetup.unregister_uninstall(winsetup.make_layout(sandbox=self.dir)))

    def tearDown(self):
        try:
            self.root.destroy()  # already gone if the test pressed Finish
        except tk.TclError:
            pass
        self.tmp.cleanup()

    def pump(self, until, seconds=15.0):
        end = time.time() + seconds
        while time.time() < end and not until():
            self.root.update()
            time.sleep(0.02)

    def texts(self):
        return [w.cget("text") for w in self.wizard.body.winfo_children() if "text" in w.keys()]

    def test_welcome_page_offers_a_default_folder_and_a_desktop_shortcut(self):
        self.assertIn("Ready to install", self.texts())
        self.assertEqual(self.wizard.primary.cget("text"), "Install")
        self.assertTrue(self.wizard.desktop_var.get())
        self.assertIn("MeetMiner", self.wizard.dir_var.get())

    def test_full_install_through_the_wizard(self):
        self.wizard.start()
        self.assertEqual(str(self.wizard.primary.cget("state")), "disabled")  # no double clicks while installing
        self.pump(lambda: self.wizard.primary.cget("text") == "Finish")
        self.assertEqual(self.wizard.primary.cget("text"), "Finish")
        self.assertTrue(any("is installed" in t for t in self.texts()))
        layout = winsetup.make_layout(Path(self.wizard.dir_var.get()), self.dir)
        self.assertTrue(layout.exe.is_file())
        self.assertTrue(layout.desktop_link.is_file())
        with mock.patch.object(installer, "launch") as launch:  # "Open now" is off in these tests
            self.wizard.launch_var.set(True)
            self.wizard.finish()
            launch.assert_called_once()

    def test_relative_folder_is_refused(self):
        self.wizard.dir_var.set("MeetMiner")
        with mock.patch.object(installer.messagebox, "showinfo") as info:
            self.wizard.start()
            info.assert_called_once()
        self.assertIsNone(self.wizard.layout)

    def test_a_folder_with_other_files_asks_first(self):
        target = self.dir / "Documents"
        target.mkdir()
        (target / "my notes.txt").write_text("keep me")
        self.wizard.dir_var.set(str(target))
        with mock.patch.object(installer.messagebox, "askyesno", return_value=False) as ask:
            self.wizard.start()
            ask.assert_called_once()
        self.assertIsNone(self.wizard.layout)
        self.assertEqual((target / "my notes.txt").read_text(), "keep me")

    def test_an_open_app_is_reported_and_the_wizard_returns_to_the_start(self):
        self.wizard.start()
        self.pump(lambda: self.wizard.primary.cget("text") == "Finish")
        self.wizard.show_welcome()
        exe = Path(self.wizard.dir_var.get()) / "MeetMiner.exe"
        with open(exe, "rb"), mock.patch.object(installer.messagebox, "showerror") as err:
            self.wizard.start()
            self.pump(lambda: err.called)
            err.assert_called_once()
            self.assertIn("is open", err.call_args.args[1])
        self.assertEqual(self.wizard.primary.cget("text"), "Install")


if __name__ == "__main__":
    unittest.main()
