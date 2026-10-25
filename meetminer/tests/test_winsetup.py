"""Checks for the install and uninstall logic. Everything happens in a temporary "sandbox" folder and one test-only
registry key: no real Start menu, Desktop or "Installed apps" entry is touched.   python -m unittest discover -s tests"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import winreg
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))  # run from a checkout without installing

from meetminer import winsetup
from meetminer.version import APP_NAME, __version__


def make_payload(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


V1 = {"MeetMiner.exe": b"MZ-pretend-exe-1", "_internal/old.dll": b"old", "_internal/keep.dll": b"1",
      "assets/icon.png": b"png"}
V2 = {"MeetMiner.exe": b"MZ-pretend-exe-2", "_internal/keep.dll": b"2", "_internal/new.dll": b"new"}


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.layout = winsetup.make_layout(sandbox=self.root)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(lambda: winsetup.unregister_uninstall(self.layout))
        self.v1 = make_payload(self.root / "v1.zip", V1)

    def registry(self, name):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, winsetup.SANDBOX_SUBKEY) as key:
            return winreg.QueryValueEx(key, name)[0]

    def test_sandbox_never_points_at_the_real_locations(self):
        real = winsetup.make_layout()
        self.assertEqual(real.registry_subkey, winsetup.UNINSTALL_SUBKEY)
        self.assertNotEqual(self.layout.registry_subkey, real.registry_subkey)
        for path in (self.layout.install_dir, self.layout.desktop_dir, self.layout.start_menu_dir):
            self.assertIn(self.root, path.parents)
        self.assertTrue(str(real.install_dir).endswith(r"Programs\MeetMiner"))

    def test_install_creates_files_shortcuts_and_an_installed_apps_entry(self):
        seen = []
        winsetup.install(self.v1, self.layout, desktop_shortcut=True, progress=lambda i, n: seen.append((i, n)))
        self.assertEqual((self.layout.exe).read_bytes(), b"MZ-pretend-exe-1")
        self.assertTrue((self.layout.install_dir / "_internal" / "old.dll").is_file())
        self.assertEqual(seen[-1], (4, 4))
        for link in (self.layout.desktop_link, self.layout.start_menu_link):
            self.assertTrue(link.is_file(), link)
        self.assertEqual(self.registry("DisplayName"), APP_NAME)
        self.assertEqual(self.registry("DisplayVersion"), __version__)
        self.assertEqual(Path(self.registry("InstallLocation")), self.layout.install_dir)
        self.assertEqual(self.registry("UninstallString"), f'"{self.layout.exe}" --uninstall --sandbox "{self.root}"')
        self.assertGreaterEqual(self.registry("EstimatedSize"), 1)

    def test_the_shortcut_points_at_the_installed_app(self):
        winsetup.install(self.v1, self.layout)
        script = "(New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK) | ForEach-Object { $_.TargetPath; $_.WorkingDirectory }"
        out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True, text=True,
                             env={**os.environ, "LNK": str(self.layout.start_menu_link)}).stdout.split("\n")
        self.assertEqual(Path(out[0].strip()).resolve(), self.layout.exe.resolve())
        self.assertEqual(Path(out[1].strip()).resolve(), self.layout.install_dir.resolve())

    def test_no_desktop_shortcut_when_declined(self):
        winsetup.install(self.v1, self.layout, desktop_shortcut=False)
        self.assertFalse(self.layout.desktop_link.exists())
        self.assertTrue(self.layout.start_menu_link.exists())

    def test_upgrade_replaces_old_files_and_keeps_one_entry(self):
        winsetup.install(self.v1, self.layout)
        winsetup.install(make_payload(self.root / "v2.zip", V2), self.layout)
        self.assertEqual(self.layout.exe.read_bytes(), b"MZ-pretend-exe-2")
        self.assertFalse((self.layout.install_dir / "_internal" / "old.dll").exists())
        self.assertEqual((self.layout.install_dir / "_internal" / "keep.dll").read_bytes(), b"2")
        self.assertTrue((self.layout.install_dir / "_internal" / "new.dll").exists())

    def test_upgrade_while_the_app_is_open_is_refused_cleanly(self):
        winsetup.install(self.v1, self.layout)
        before = sorted(p.relative_to(self.layout.install_dir) for p in self.layout.install_dir.rglob("*"))
        with open(self.layout.exe, "rb"):  # a file in use blocks renaming its folder, as a running program does
            with self.assertRaises(winsetup.AppRunning):
                winsetup.install(make_payload(self.root / "v2.zip", V2), self.layout)
        after = sorted(p.relative_to(self.layout.install_dir) for p in self.layout.install_dir.rglob("*"))
        self.assertEqual(before, after)  # nothing was deleted or half-replaced
        self.assertEqual(self.layout.exe.read_bytes(), b"MZ-pretend-exe-1")
        leftovers = [p.name for p in self.layout.install_dir.parent.iterdir() if p != self.layout.install_dir]
        self.assertEqual(leftovers, [])  # the half-unpacked new version was cleaned away
        winsetup.install(make_payload(self.root / "v2.zip", V2), self.layout)  # and once the app is closed it works
        self.assertEqual(self.layout.exe.read_bytes(), b"MZ-pretend-exe-2")

    def test_installing_into_a_folder_with_other_files_never_deletes_them(self):
        self.layout.install_dir.mkdir(parents=True)
        (self.layout.install_dir / "my notes.txt").write_text("keep me")
        winsetup.install(self.v1, self.layout)
        self.assertEqual((self.layout.install_dir / "my notes.txt").read_text(), "keep me")
        self.assertTrue(self.layout.exe.is_file())

    def test_a_payload_cannot_write_outside_the_install_folder(self):
        evil = make_payload(self.root / "evil.zip", {"MeetMiner.exe": b"x", "../escaped.txt": b"no"})
        with self.assertRaises(ValueError):
            winsetup.extract_payload(evil, self.layout.install_dir)
        self.assertFalse((self.layout.install_dir.parent / "escaped.txt").exists())

    def test_uninstall_removes_everything_it_installed_but_leaves_transcripts(self):
        transcripts = self.root / "Documents" / "Transcripts"
        transcripts.mkdir(parents=True)
        (transcripts / "call.txt").write_text("Speaker 1  00:00\nhello\n", encoding="utf-8")
        data = self.root / "appdata"
        data.mkdir()
        (data / "settings.json").write_text('{"api_key": "dpapi:xx"}', encoding="utf-8")
        winsetup.install(self.v1, self.layout)
        with mock.patch.dict(os.environ, {"MEETMINER_HOME": str(data)}), \
                mock.patch.object(winsetup, "schedule_folder_removal") as later:
            winsetup.uninstall(self.layout, remove_user_data=True)
            later.assert_called_once_with(self.layout.install_dir)
        self.assertFalse(self.layout.desktop_link.exists())
        self.assertFalse(self.layout.start_menu_link.exists())
        self.assertFalse(self.layout.start_menu_dir.exists())
        self.assertFalse(data.exists())  # settings and the saved key are gone
        self.assertTrue((transcripts / "call.txt").exists())  # the user's transcripts are never deleted
        with self.assertRaises(OSError):
            self.registry("DisplayName")

    def test_uninstall_can_keep_the_user_data(self):
        data = self.root / "appdata"
        data.mkdir()
        (data / "settings.json").write_text("{}", encoding="utf-8")
        winsetup.install(self.v1, self.layout)
        with mock.patch.dict(os.environ, {"MEETMINER_HOME": str(data)}):
            winsetup.uninstall(self.layout, remove_user_data=False, remove_folder=False)
        self.assertTrue((data / "settings.json").exists())

    def test_the_install_folder_is_removed_after_the_program_exits(self):
        with mock.patch("subprocess.Popen") as popen:
            winsetup.schedule_folder_removal(Path(r"C:\Some Place\MeetMiner"))
        script = Path(popen.call_args.args[0][2])
        text = script.read_text(encoding="ascii")
        script.unlink()
        self.assertIn('rmdir /s /q "C:\\Some Place\\MeetMiner"', text)
        self.assertIn("ping", text)  # a short wait, so the program has exited before its folder goes

    def test_layout_is_found_again_from_the_registry(self):
        self.assertIsNone(winsetup.layout_from_registry(self.root))
        winsetup.install(self.v1, self.layout)
        found = winsetup.layout_from_registry(self.root)
        self.assertEqual(found.install_dir, self.layout.install_dir)
        self.assertEqual(found.desktop_link, self.layout.desktop_link)


if __name__ == "__main__":
    unittest.main()
