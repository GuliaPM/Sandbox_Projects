"""MeetMiner setup. PyInstaller turns this into MeetMiner-Setup.exe with the app inside (payload.zip).

    MeetMiner-Setup.exe                      the wizard
    MeetMiner-Setup.exe /S                   silent install for the current user (also: --silent)
    options:  --dir FOLDER   --no-desktop   --no-launch
"""
from __future__ import annotations

import argparse
import ctypes
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))  # running from a checkout: find the meetminer package

from meetminer import winsetup  # noqa: E402
from meetminer.version import APP_NAME, __version__  # noqa: E402

RES = Path(getattr(sys, "_MEIPASS", HERE))  # where PyInstaller unpacks the bundled files
ACCENT, DARK, MUTED = "#2563eb", "#111827", "#6b7280"


def find_payload(override: str | None) -> Path:
    return Path(override) if override else RES / "payload.zip"


def launch(layout: winsetup.Layout) -> None:
    subprocess.Popen([str(layout.exe)], cwd=str(layout.install_dir), close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


def run_silent(args, payload: Path) -> int:
    layout = winsetup.make_layout(Path(args.dir) if args.dir else None, Path(args.sandbox) if args.sandbox else None)
    try:
        winsetup.install(payload, layout, desktop_shortcut=not args.no_desktop)
    except winsetup.AppRunning:
        return 3
    except Exception:  # a silent run reports failure through its exit code
        return 1
    if not args.no_launch:
        launch(layout)
    return 0


class Wizard:
    def __init__(self, root: tk.Tk, payload: Path, args):
        self.root, self.payload, self.args = root, payload, args
        self.events: queue.Queue = queue.Queue()
        self.layout: winsetup.Layout | None = None
        root.title(f"{APP_NAME} Setup")
        root.configure(bg="#ffffff")
        root.resizable(False, False)
        scale = root.winfo_fpixels("1i") / 96
        w, h = int(640 * scale), int(440 * scale)
        root.geometry(f"{w}x{h}+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 3}")
        try:
            self.icon = tk.PhotoImage(file=str(RES / "icon.png"))
            root.iconphoto(True, self.icon)
            self.logo = self.icon.subsample(4)  # 64 pixels
        except tk.TclError:
            self.logo = None

        header = tk.Frame(root, bg=ACCENT)
        header.pack(fill="x")
        if self.logo:
            tk.Label(header, image=self.logo, bg=ACCENT).pack(side="left", padx=(24, 14), pady=14)
        titles = tk.Frame(header, bg=ACCENT)
        titles.pack(side="left", pady=14)
        tk.Label(titles, text=APP_NAME, bg=ACCENT, fg="#ffffff", font=("Segoe UI", 20, "bold"), anchor="w").pack(fill="x")
        tk.Label(titles, text=f"Version {__version__}  ·  search your Otter.ai transcripts in plain language",
                 bg=ACCENT, fg="#dbeafe", font=("Segoe UI", 10), anchor="w").pack(fill="x")

        self.footer = tk.Frame(root, bg="#f3f4f6")
        self.footer.pack(side="bottom", fill="x")
        self.body = tk.Frame(root, bg="#ffffff")
        self.body.pack(fill="both", expand=True, padx=28, pady=18)
        self.primary = tk.Button(self.footer, relief="flat", bg=ACCENT, fg="#ffffff", font=("Segoe UI", 10), padx=22, pady=7,
                                 cursor="hand2")
        self.secondary = tk.Button(self.footer, relief="flat", font=("Segoe UI", 10), padx=18, pady=7, cursor="hand2")
        self.primary.pack(side="right", padx=(8, 20), pady=12)
        self.secondary.pack(side="right", pady=12)
        self.show_welcome()
        root.after(100, self._poll)

    # ------------------------------------------------------------------ pages

    def clear(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()

    def label(self, text: str, size: int = 10, bold: bool = False, fg: str = DARK, pady=(0, 0)) -> tk.Label:
        lab = tk.Label(self.body, text=text, bg="#ffffff", fg=fg, font=("Segoe UI", size, "bold" if bold else "normal"),
                       anchor="w", justify="left", wraplength=560)
        lab.pack(fill="x", pady=pady)
        return lab

    def show_welcome(self) -> None:
        self.clear()
        self.label("Ready to install", 14, True)
        self.label("Ask questions about your meetings and get answers with the exact quote from the transcript "
                   "highlighted. This installs for your Windows account only; no administrator rights are needed.",
                   pady=(6, 14))
        self.label("Install to", 9, True, MUTED)
        row = tk.Frame(self.body, bg="#ffffff")
        row.pack(fill="x", pady=(2, 10))
        self.dir_var = tk.StringVar(value=self.args.dir or str(winsetup.default_install_dir()))
        tk.Entry(row, textvariable=self.dir_var, font=("Segoe UI", 10), relief="solid", bd=1).pack(
            side="left", fill="x", expand=True, ipady=4)
        tk.Button(row, text="Browse…", command=self.browse, relief="flat", bg="#e5e7eb", padx=12, pady=3,
                  cursor="hand2").pack(side="left", padx=(8, 0))
        self.desktop_var = tk.BooleanVar(value=not self.args.no_desktop)
        tk.Checkbutton(self.body, text="Add a shortcut to the Desktop", variable=self.desktop_var, bg="#ffffff",
                       font=("Segoe UI", 10), anchor="w").pack(fill="x")
        self.label("The first time you open the app it asks for your own Anthropic API key and your Otter transcripts "
                   "(.txt exports). Neither is included here.", 9, False, MUTED, pady=(16, 0))
        self.primary.configure(text="Install", command=self.start, state="normal")
        self.secondary.configure(text="Cancel", command=self.root.destroy)

    def browse(self) -> None:
        chosen = filedialog.askdirectory(parent=self.root, title="Choose where to install", initialdir=self.dir_var.get())
        if chosen:
            self.dir_var.set(str(Path(chosen) / APP_NAME) if Path(chosen).name != APP_NAME else chosen)

    def start(self) -> None:
        target = Path(self.dir_var.get().strip().strip('"'))
        if not target.is_absolute() or target.parent == target:
            messagebox.showinfo(f"{APP_NAME} Setup", "Please choose a full folder path, for example "
                                f"{winsetup.default_install_dir()}.", parent=self.root)
            return
        existing = target.exists() and any(target.iterdir())
        if existing and not (target / winsetup.APP_EXE).exists() and not messagebox.askyesno(
                f"{APP_NAME} Setup", f"{target} already has other files in it. Install here anyway?", parent=self.root):
            return
        self.layout = winsetup.make_layout(target, Path(self.args.sandbox) if self.args.sandbox else None)
        self.clear()
        self.label("Installing…", 14, True)
        self.status = self.label("Preparing", 10, False, MUTED, pady=(6, 14))
        self.bar = ttk.Progressbar(self.body, mode="determinate", maximum=100)
        self.bar.pack(fill="x")
        self.primary.configure(state="disabled")
        self.secondary.configure(state="disabled")
        threading.Thread(target=self._install, args=(self.desktop_var.get(),), daemon=True).start()

    def _install(self, desktop: bool) -> None:
        try:
            winsetup.install(self.payload, self.layout, desktop_shortcut=desktop,
                             progress=lambda i, n: self.events.put(("progress", i, n)))
            self.events.put(("done",))
        except winsetup.AppRunning as exc:
            self.events.put(("error", str(exc)))
        except Exception as exc:
            self.events.put(("error", f"Setup could not finish:\n{exc}"))

    def _poll(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "progress":
                    self.bar["value"] = 100 * event[1] / event[2]
                    self.status.configure(text=f"Copying files ({event[1]} of {event[2]})")
                elif event[0] == "done":
                    self.show_done()
                elif event[0] == "error":
                    messagebox.showerror(f"{APP_NAME} Setup", event[1], parent=self.root)
                    self.show_welcome()
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def show_done(self) -> None:
        self.clear()
        self.label(f"{APP_NAME} is installed", 14, True)
        self.label("Open it from the Start menu or the Desktop shortcut. The first time, it will ask for your Anthropic "
                   "API key and your transcripts.", pady=(6, 14))
        self.launch_var = tk.BooleanVar(value=not self.args.no_launch)
        tk.Checkbutton(self.body, text=f"Open {APP_NAME} now", variable=self.launch_var, bg="#ffffff",
                       font=("Segoe UI", 10), anchor="w").pack(fill="x")
        self.primary.configure(text="Finish", command=self.finish, state="normal")
        self.secondary.pack_forget()

    def finish(self) -> None:
        if self.launch_var.get():
            launch(self.layout)
        self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    argv = ["--silent" if a.lower() in ("/s", "/silent") else a for a in (sys.argv[1:] if argv is None else argv)]
    parser = argparse.ArgumentParser(description=f"{APP_NAME} setup")
    parser.add_argument("--silent", action="store_true", help="install without a window")
    parser.add_argument("--dir", help="install folder (default: %%LOCALAPPDATA%%\\Programs\\MeetMiner)")
    parser.add_argument("--no-desktop", action="store_true", help="do not add a Desktop shortcut")
    parser.add_argument("--no-launch", action="store_true", help="do not open the app afterwards")
    parser.add_argument("--payload", help=argparse.SUPPRESS)
    parser.add_argument("--sandbox", help=argparse.SUPPRESS)  # tests: shortcuts and the registry entry go in this folder
    args = parser.parse_args(argv)
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    payload = find_payload(args.payload)
    if not payload.is_file():
        if not args.silent:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(f"{APP_NAME} Setup", "This setup file is damaged (the app files are missing). "
                                 "Please download it again.")
        return 2
    if args.silent:
        return run_silent(args, payload)
    root = tk.Tk()
    Wizard(root, payload, args)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
