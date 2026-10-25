"""Install and uninstall helpers for Windows, shared by the setup program and the app's own --uninstall mode.

The app installs for the current Windows user only (no administrator rights): files in
%LOCALAPPDATA%\\Programs\\MeetMiner, shortcuts in the Start menu and on the Desktop, and one entry in
"Installed apps" (HKEY_CURRENT_USER). Uninstalling removes all of that, and optionally the user's settings.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import tkinter as tk
import winreg
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from tkinter import messagebox
from typing import Callable

from . import storage
from .version import APP_NAME, __version__

APP_EXE = "MeetMiner.exe"
UNINSTALL_SUBKEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\MeetMiner"
SANDBOX_SUBKEY = r"Software\MeetMinerSetupTest"  # tests keep their registry entry here, away from the real list
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

FOLDERID_DESKTOP = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"
FOLDERID_PROGRAMS = "{A77F5D77-2E2B-44C3-A6A2-ABA601054A51}"  # Start menu > Programs
FOLDERID_LOCAL_APPDATA = "{F1B32785-6FBA-4FCF-9D55-7B8E7F157091}"


class AppRunning(Exception):
    """The installed app is open, so its files cannot be replaced."""


def known_folder(folder_id: str, fallback: Path) -> Path:
    try:
        class GUID(ctypes.Structure):
            _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

        guid = GUID()
        ctypes.windll.ole32.CLSIDFromString(folder_id, ctypes.byref(guid))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0:
            path = Path(out.value)
            ctypes.windll.ole32.CoTaskMemFree(out)
            return path
    except (AttributeError, OSError):
        pass
    return fallback


@dataclass
class Layout:
    install_dir: Path
    desktop_dir: Path
    start_menu_dir: Path  # the folder that holds the Start menu shortcut
    registry_subkey: str
    sandbox: Path | None = None

    @property
    def exe(self) -> Path:
        return self.install_dir / APP_EXE

    @property
    def desktop_link(self) -> Path:
        return self.desktop_dir / f"{APP_NAME}.lnk"

    @property
    def start_menu_link(self) -> Path:
        return self.start_menu_dir / f"{APP_NAME}.lnk"


def default_install_dir() -> Path:
    local = known_folder(FOLDERID_LOCAL_APPDATA, Path.home() / "AppData" / "Local")
    return local / "Programs" / APP_NAME


def make_layout(install_dir: Path | None = None, sandbox: Path | None = None) -> Layout:
    """Real Windows locations, or (for tests) everything inside the `sandbox` folder."""
    if sandbox:
        return Layout(install_dir or sandbox / "Program Files" / APP_NAME, sandbox / "Desktop",
                      sandbox / "Start Menu" / APP_NAME, SANDBOX_SUBKEY, sandbox)
    desktop = known_folder(FOLDERID_DESKTOP, Path.home() / "Desktop")
    programs = known_folder(FOLDERID_PROGRAMS, Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs")
    return Layout(install_dir or default_install_dir(), desktop, programs / APP_NAME, UNINSTALL_SUBKEY)


def layout_from_registry(sandbox: Path | None = None) -> Layout | None:
    """Where the app was installed, as recorded when it was installed."""
    base = make_layout(sandbox=sandbox)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base.registry_subkey) as key:
            location = winreg.QueryValueEx(key, "InstallLocation")[0]
    except OSError:
        return None
    return make_layout(Path(location), sandbox)


# --------------------------------------------------------------------------- installing


def create_shortcut(link: Path, target: Path, workdir: Path, description: str) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    script = ("$s = New-Object -ComObject WScript.Shell; $l = $s.CreateShortcut($env:MS_LNK); "
              "$l.TargetPath = $env:MS_TARGET; $l.WorkingDirectory = $env:MS_WORKDIR; "
              "$l.IconLocation = $env:MS_TARGET + ',0'; $l.Description = $env:MS_DESC; $l.Save()")
    env = {**os.environ, "MS_LNK": str(link), "MS_TARGET": str(target), "MS_WORKDIR": str(workdir), "MS_DESC": description}
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                   env=env, check=True, capture_output=True, creationflags=NO_WINDOW)


def _unpack(payload: Path, folder: Path, progress: Callable[[int, int], None] | None) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    root = folder.resolve()
    total = 0
    with zipfile.ZipFile(payload) as z:
        members = z.infolist()
        for i, member in enumerate(members, 1):
            target = (folder / member.filename).resolve()
            if root != target and root not in target.parents:  # never write outside the install folder
                raise ValueError(f"unsafe path in the setup file: {member.filename}")
            z.extract(member, folder)
            total += member.file_size
            if progress:
                progress(i, len(members))
    return total


def extract_payload(payload: Path, install_dir: Path, progress: Callable[[int, int], None] | None = None) -> int:
    """Unpack the app into install_dir. Returns the size in bytes.

    A new install just unpacks. An upgrade unpacks the new version beside the old one and then swaps the two folders.
    Windows will not rename a folder whose program is running, so if the app is open the swap is refused with
    AppRunning and the installed version is exactly as it was (nothing is half-deleted)."""
    if not (install_dir / APP_EXE).exists():
        return _unpack(payload, install_dir, progress)
    staging = install_dir.with_name(install_dir.name + ".new")
    previous = install_dir.with_name(install_dir.name + ".old")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(previous, ignore_errors=True)
    try:
        total = _unpack(payload, staging, progress)
        try:
            os.rename(install_dir, previous)
        except OSError as exc:
            raise AppRunning(f"{APP_NAME} is open. Close it and run setup again.") from exc
        os.rename(staging, install_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(previous, ignore_errors=True)
    return total


def register_uninstall(layout: Layout, size_bytes: int) -> None:
    command = f'"{layout.exe}" --uninstall' + (f' --sandbox "{layout.sandbox}"' if layout.sandbox else "")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, layout.registry_subkey) as key:
        for name, value in (("DisplayName", APP_NAME), ("DisplayVersion", __version__), ("Publisher", APP_NAME),
                            ("InstallLocation", str(layout.install_dir)), ("DisplayIcon", str(layout.exe)),
                            ("UninstallString", command), ("InstallDate", date.today().strftime("%Y%m%d"))):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "EstimatedSize", 0, winreg.REG_DWORD, max(size_bytes // 1024, 1))
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)


def install(payload: Path, layout: Layout, desktop_shortcut: bool = True,
            progress: Callable[[int, int], None] | None = None) -> None:
    size = extract_payload(payload, layout.install_dir, progress)
    create_shortcut(layout.start_menu_link, layout.exe, layout.install_dir, "Ask questions about your Otter transcripts")
    if desktop_shortcut:
        create_shortcut(layout.desktop_link, layout.exe, layout.install_dir, "Ask questions about your Otter transcripts")
    register_uninstall(layout, size)


# --------------------------------------------------------------------------- uninstalling


def remove_shortcuts(layout: Layout) -> None:
    layout.desktop_link.unlink(missing_ok=True)
    layout.start_menu_link.unlink(missing_ok=True)
    try:
        layout.start_menu_dir.rmdir()  # only if it is empty
    except OSError:
        pass


def unregister_uninstall(layout: Layout) -> None:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, layout.registry_subkey)
    except OSError:
        pass


def schedule_folder_removal(folder: Path) -> None:
    """The running program lives in this folder, so a small script deletes it a few seconds after we exit."""
    script = Path(tempfile.gettempdir()) / "meetminer-cleanup.bat"
    script.write_text(f'@echo off\r\nping 127.0.0.1 -n 4 >nul\r\nrmdir /s /q "{folder}"\r\ndel "%~f0"\r\n', encoding="ascii")
    subprocess.Popen(["cmd.exe", "/c", str(script)], creationflags=NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0),
                     close_fds=True)


def uninstall(layout: Layout, remove_user_data: bool, remove_folder: bool = True) -> None:
    remove_shortcuts(layout)
    unregister_uninstall(layout)
    if remove_user_data:
        shutil.rmtree(storage.data_dir(), ignore_errors=True)
    if remove_folder and layout.install_dir.exists():
        schedule_folder_removal(layout.install_dir)


def uninstall_main(sandbox: Path | None = None, silent: bool = False, remove_user_data: bool = True) -> int:
    """The window shown by "Uninstall" in Windows Settings (and `MeetMiner.exe --uninstall`)."""
    layout = layout_from_registry(sandbox)
    if layout is None and getattr(sys, "frozen", False):
        layout = make_layout(Path(sys.executable).parent, sandbox)
    if layout is None:
        if not silent:
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(APP_NAME, f"{APP_NAME} does not seem to be installed.")
        return 1
    if silent:
        uninstall(layout, remove_user_data)
        return 0

    root = tk.Tk()
    root.title(f"Uninstall {APP_NAME}")
    root.configure(bg="#ffffff", padx=28, pady=22)
    root.resizable(False, False)
    try:
        icon = tk.PhotoImage(file=str(storage.resource_dir() / "assets" / "icon.png"))
        root.iconphoto(True, icon)
    except tk.TclError:
        pass
    delete_data = tk.BooleanVar(value=True)
    transcripts = storage.default_transcripts_dir()
    tk.Label(root, text=f"Remove {APP_NAME}?", bg="#ffffff", font=("Segoe UI", 14, "bold"), anchor="w").pack(fill="x")
    message = tk.Label(root, bg="#ffffff", fg="#374151", font=("Segoe UI", 10), anchor="w", justify="left", wraplength=440,
                       text=f"This removes the app from {layout.install_dir}, its shortcuts, and its entry in Installed apps.\n\n"
                            f"Your transcripts are not touched: they stay in {transcripts} (or the folder you chose).")
    message.pack(fill="x", pady=(10, 8))
    check = tk.Checkbutton(root, variable=delete_data, bg="#ffffff", font=("Segoe UI", 10), anchor="w", justify="left",
                           text="Also delete my settings, usage log and saved API key")
    check.pack(fill="x")
    buttons = tk.Frame(root, bg="#ffffff")
    buttons.pack(fill="x", pady=(18, 0))

    def run() -> None:
        try:
            uninstall(layout, delete_data.get())
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not finish removing the app:\n{exc}")
            return
        check.pack_forget()
        message.configure(text=f"{APP_NAME} has been removed.")
        go.pack_forget()
        cancel.configure(text="Close")

    go = tk.Button(buttons, text="Uninstall", command=run, bg="#2563eb", fg="#ffffff", relief="flat", padx=16, pady=6,
                   font=("Segoe UI", 10))
    go.pack(side="right")
    cancel = tk.Button(buttons, text="Cancel", command=root.destroy, relief="flat", padx=16, pady=6, font=("Segoe UI", 10))
    cancel.pack(side="right", padx=8)
    root.mainloop()
    return 0
