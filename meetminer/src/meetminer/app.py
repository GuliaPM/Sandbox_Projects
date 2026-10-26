"""MeetMiner as a desktop window (no browser).

    Installed:    open "MeetMiner" from the Start menu or the Desktop.
    From source:  python -m meetminer          python -m meetminer --ask "What decisions were made?"
"""
from __future__ import annotations

import argparse
import gc
import os
import queue
import re
import shutil
import sys
import threading
import tkinter as tk
import traceback
import webbrowser
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tkinter import filedialog
from tkinter import font as tkfont
from tkinter import messagebox, ttk

import anthropic

from . import guardrails, storage
from .engine import (ENGINES, Answer, ConfigError, CostTracker, MeetingQA, Settings, has_credentials, load_env,
                     load_settings, with_overrides)
from .reports_ui import ReportsPanel
from .transcripts import CITE_RE, Meeting, load_meetings
from .version import APP_NAME, __version__

KEYS_URL = "https://console.anthropic.com/settings/keys"

C = {  # colours
    "bg": "#ffffff", "side": "#f5f6f8", "hover": "#e8ebf0", "line": "#e5e7eb", "text": "#111827",
    "muted": "#6b7280", "accent": "#2563eb", "accent_dark": "#1d4ed8", "user_bg": "#eef3ff",
    "ok_bg": "#dcfce7", "ok_fg": "#166534", "weak_bg": "#fef3c7", "weak_fg": "#92400e",
    "bad_bg": "#fee2e2", "bad_fg": "#991b1b", "focus": "#fff7d6", "quote": "#ffe066", "code_bg": "#f1f2f4",
}
CHIP = {"ok": "✓", "weak": "!", "bad": "✕"}  # check mark, !, cross
MODEL_LABELS = {
    "claude-haiku-5-5": "Haiku 5.5  ·  cheapest",
    "claude-sonnet-5-5": "Sonnet 5.5  ·  most accurate",
    "claude-opus-5-5": "Opus 5.5  ·  most expensive",
}
DEFAULT_EXAMPLES = [
    "What decisions were made in the most recent meeting?",
    "What action items were assigned, and to whom?",
    "Did anyone mention a deadline or a budget?",
]


# --------------------------------------------------------------------------- pure helpers (tested without a window)


def block_kind(line: str) -> tuple[str, str, int, str]:
    """(kind, text, level, marker) for one line of the model's light markdown."""
    s = line.rstrip()
    if not s.strip() or re.fullmatch(r"\s*[-*_]{3,}\s*", s):
        return "blank", "", 0, ""
    m = re.match(r"^(#{1,6})\s+(.*)$", s)
    if m:
        return "heading", m.group(2), len(m.group(1)), ""
    m = re.match(r"^(\s*)[-*•]\s+(.*)$", s)
    if m:
        return "bullet", m.group(2), min(len(m.group(1)) // 2, 3), ""
    m = re.match(r"^(\s*)(\d+)[.)]\s+(.*)$", s)
    if m:
        return "number", m.group(3), min(len(m.group(1)) // 2, 3), m.group(2)
    if s.lstrip().startswith("|"):
        return "table", s, 0, ""
    return "para", s, 0, ""


_INLINE_RE = re.compile(
    r"(\*\*[^*\n]+?\*\*|`[^`\n]+`|(?<![*\w])\*(?=[^\s*])[^*\n]*?(?<=[^\s*])\*(?![*\w]))"
)


def inline_runs(text: str) -> list[tuple[str, str | None]]:
    """Split text into (piece, style) where style is None, 'bold', 'italic' or 'code'."""
    out: list[tuple[str, str | None]] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            out.append((text[pos:m.start()], None))
        tok = m.group(0)
        if tok.startswith("**"):
            out.append((tok[2:-2], "bold"))
        elif tok.startswith("`"):
            out.append((tok[1:-1], "code"))
        else:
            out.append((tok[1:-1], "italic"))
        pos = m.end()
    if pos < len(text):
        out.append((text[pos:], None))
    return out


_ELLIPSIS = re.compile(r"\.\.\.|…")


def quote_spans(text: str, quote: str) -> list[tuple[int, int]]:
    """Where in `text` the quoted words are. Matches through punctuation, case and Otter's repeated words
    ("our our"), so a quote the checker accepted can be highlighted in the original wording."""
    spans: list[tuple[int, int]] = []
    haystack = text.replace("’", "'").replace("‘", "'")  # same length, so offsets still fit `text`
    for frag in _ELLIPSIS.split(quote):
        frag = frag.replace("’", "'").replace("‘", "'")
        words = [w.strip("'") for w in re.findall(r"[\w']+", frag)]
        words = [w for w in words if w]
        if len(words) < 2:
            continue
        body = r"[\W_]+".join(rf"{re.escape(w)}(?:[\W_]+{re.escape(w)})*" for w in words)
        lead = r"\w*'" if frag.strip().startswith("'") else r"(?<![\w'])"
        m = re.search(lead + body, haystack, re.IGNORECASE)
        if m:
            spans.append(m.span())
    return spans


def unique_name(folder: Path, name: str) -> Path:
    """folder/name, or 'name (2)' etc. if a file with that name is already there (never overwrite a transcript)."""
    target = folder / name
    stem, suffix, n = target.stem, target.suffix, 2
    while target.exists():
        target = folder / f"{stem} ({n}){suffix}"
        n += 1
    return target


def folder_signature(folder: Path) -> tuple:
    """Names, sizes and times of the files in a folder, to notice when transcripts were added or changed."""
    try:
        return tuple(sorted((p.name, p.stat().st_size, int(p.stat().st_mtime)) for p in folder.iterdir() if p.is_file()))
    except OSError:
        return ()


def caption_for(ans: Answer) -> str:
    t = ans.tokens
    head = f"{ans.model}  ·  {ans.seconds:.1f}s  ·  ${ans.cost:.4f}   "
    if ans.engine == "rag" and ans.retrieved is not None:
        r = ans.retrieved
        if not r.turns:
            return "RAG  ·  no passage matched, nothing was sent  ·  $0.0000"
        n = len(r.meetings)
        return head + (f"(RAG: {len(r.turns)} passages from {n} meeting{'s' if n != 1 else ''}, "
                       f"input {t['input']:,}, output {t['output']:,})")
    return head + (f"(full context: new input {t['input']:,}, from cache {t['cache_read']:,}, "
                   f"written to cache {t['cache_write']:,}, output {t['output']:,})")


# --------------------------------------------------------------------------- the window


class Writable:
    """`with Writable(text_widget) as w:` lets code edit a read-only Text widget."""

    def __init__(self, widget: tk.Text):
        self.widget = widget

    def __enter__(self) -> tk.Text:
        self.widget.configure(state="normal")
        return self.widget

    def __exit__(self, *exc) -> None:
        self.widget.configure(state="disabled")


class App:
    def __init__(self, root: tk.Tk, settings: Settings, loaded, env_note: str | None = None,
                 first_question: str | None = None):
        self.root = root
        self.base_settings = settings
        self.settings = settings
        self.loaded = loaded
        self.env_note = env_note
        self.tracker = CostTracker(settings)
        self.qa = MeetingQA(settings, loaded.meetings, self.tracker)
        self.history: list[dict] = []  # {"q": str, "ans": Answer}
        self.busy = False
        self.results: queue.Queue = queue.Queue()
        self.prompt_tokens: tuple[int, str] = (0, "estimated")
        self.answer_count = 0
        self.setup_visible = False  # True while the "two quick steps" checklist is on screen
        self.spend_blocked = False  # a daily or monthly limit has been reached (set by refresh_notices)
        self.scale = root.winfo_fpixels("1i") / 96
        self._folder_sig = folder_signature(settings.transcripts_dir)

        self._make_fonts()
        self._build()
        self.refresh_meetings()
        self.update_cost()
        self.show_welcome()  # decides whether the first-run checklist is showing...
        self.refresh_notices()  # ...so the top bar does not repeat it
        self.show_placeholder()
        self._poll_id = self.root.after(100, self._poll)
        threading.Thread(target=self._count_tokens, daemon=True).start()
        if first_question:
            self.root.after(500, lambda: self.ask(first_question))

    # ------------------------------------------------------------------ building the window

    def px(self, n: int) -> int:
        return int(n * self.scale)

    def _make_fonts(self) -> None:
        def f(size, **kw):
            return tkfont.Font(root=self.root, family="Segoe UI", size=size, **kw)

        self.F = {
            "body": f(11), "bold": f(11, weight="bold"), "italic": f(11, slant="italic"),
            "small": f(9), "small_bold": f(9, weight="bold"), "chip": f(9, weight="bold"),
            "h1": f(22, weight="bold"), "h2": f(14, weight="bold"), "h3": f(12, weight="bold"),
            "side": f(10), "side_bold": f(10, weight="bold"), "money": f(20, weight="bold"),
            "code": tkfont.Font(root=self.root, family="Consolas", size=10),
        }

    def button(self, parent, text, command, primary=False, **kw) -> tk.Button:
        bg, fg = (C["accent"], "#ffffff") if primary else (C["bg"], C["text"])
        b = tk.Button(parent, text=text, command=command, bg=bg, fg=fg, activebackground=C["accent_dark"] if primary
                      else C["hover"], activeforeground=fg, relief="flat", bd=0, cursor="hand2", font=self.F["side"],
                      highlightthickness=0 if primary else 1, highlightbackground=C["line"], padx=12, pady=7, **kw)
        return b

    def _build(self) -> None:
        r = self.root
        r.title(APP_NAME)
        r.configure(bg=C["bg"])
        try:
            self.icon = tk.PhotoImage(file=str(storage.resource_dir() / "assets" / "icon.png"))
            r.iconphoto(True, self.icon)
        except tk.TclError:
            pass
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        w, h = min(self.px(1320), int(sw * 0.9)), min(self.px(780), int(sh * 0.82))  # leaves room for the taskbar
        r.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(int(sh * 0.03), 0)}")
        r.minsize(self.px(900), self.px(600))

        self.main = ttk.PanedWindow(r, orient="horizontal")
        self.main.pack(fill="both", expand=True)
        self.side = tk.Frame(self.main, bg=C["side"], width=self.px(262))
        self.center = tk.Frame(self.main, bg=C["bg"])
        self.right = tk.Frame(self.main, bg=C["bg"])
        self.main.add(self.side, weight=0)
        self.main.add(self.center, weight=3)
        self.main.add(self.right, weight=2)
        self.side.pack_propagate(False)
        self._build_side()
        self._build_center()
        self._build_right()
        r.protocol("WM_DELETE_WINDOW", self.close)
        r.lift()  # open in front of other windows, then let it behave normally
        r.attributes("-topmost", True)
        r.bind("<Control-n>", lambda e: self.new_chat())
        r.bind("<Control-l>", lambda e: self.input.focus_set())
        r.bind("<FocusIn>", self._on_focus)
        r.after(80, self._settle_layout, w)

    def _settle_layout(self, width: int) -> None:
        """Runs just after the window appears: pane widths, and stop being 'always on top'."""
        if not self.root.winfo_exists():
            return
        self.main.sashpos(0, self.px(262))
        self.main.sashpos(1, int(width * 0.64))
        self.root.after(350, lambda: self.root.winfo_exists() and self.root.attributes("-topmost", False))

    def _build_side(self) -> None:
        s = self.side
        tk.Label(s, text=APP_NAME, bg=C["side"], fg=C["text"], font=self.F["h2"], anchor="w").pack(
            fill="x", padx=18, pady=(18, 2))
        tk.Label(s, text="Ask questions about your meetings", bg=C["side"], fg=C["muted"], font=self.F["small"],
                 anchor="w").pack(fill="x", padx=18, pady=(0, 14))
        self.button(s, "+  New chat", self.new_chat, primary=True).pack(fill="x", padx=18, pady=(0, 16))

        tk.Label(s, text="MEETINGS", bg=C["side"], fg=C["muted"], font=self.F["small_bold"], anchor="w").pack(
            fill="x", padx=18, pady=(0, 4))
        self.meeting_box = tk.Frame(s, bg=C["side"])
        self.meeting_box.pack(fill="x", padx=10)

        bottom = tk.Frame(s, bg=C["side"])
        bottom.pack(side="bottom", fill="x", padx=18, pady=(0, 12))
        tk.Label(bottom, text=f"Version {__version__}", bg=C["side"], fg=C["muted"], font=self.F["small"],
                 anchor="w").pack(side="bottom", fill="x", pady=(10, 0))
        self.button(bottom, "Settings", self.open_settings).pack(side="bottom", fill="x", pady=(6, 0))
        self.button(bottom, "Reload transcripts", self.reload).pack(fill="x", pady=(10, 0))
        self.tokens_label = tk.Label(bottom, text="", bg=C["side"], fg=C["muted"], font=self.F["small"], anchor="w")
        self.tokens_label.pack(fill="x", pady=(8, 0))
        tk.Label(bottom, text="SESSION COST", bg=C["side"], fg=C["muted"], font=self.F["small_bold"], anchor="w").pack(
            fill="x", pady=(14, 0))
        self.cost_label = tk.Label(bottom, text="$0.0000", bg=C["side"], fg=C["text"], font=self.F["money"], anchor="w")
        self.cost_label.pack(fill="x")
        self.cost_sub = tk.Label(bottom, text="", bg=C["side"], fg=C["muted"], font=self.F["small"], anchor="w")
        self.cost_sub.pack(fill="x")

        models = [m for m, v in self.settings.price_table.items() if isinstance(v, dict)]
        self.model_ids = {MODEL_LABELS.get(m, m): m for m in models}
        tk.Label(bottom, text="ENGINE", bg=C["side"], fg=C["muted"], font=self.F["small_bold"], anchor="w").pack(
            fill="x", pady=(14, 4))
        switch = tk.Frame(bottom, bg=C["line"], padx=1, pady=1)  # a one-pixel frame around the two halves
        switch.pack(fill="x")
        self.engine_buttons: dict[str, tk.Button] = {}
        for key, label in ENGINES.items():
            b = tk.Button(switch, text=label, command=lambda k=key: self.set_engine(k), relief="flat", bd=0,
                          cursor="hand2", font=self.F["side"], padx=6, pady=5)
            b.pack(side="left", fill="x", expand=True)
            self.engine_buttons[key] = b
        self.engine_note = tk.Label(bottom, text="", bg=C["side"], fg=C["muted"], font=self.F["small"], anchor="w")
        self.engine_note.pack(fill="x", pady=(3, 0))
        self._style_engine()

        tk.Label(bottom, text="ANSWER MODEL", bg=C["side"], fg=C["muted"], font=self.F["small_bold"], anchor="w").pack(
            fill="x", pady=(14, 4))
        self.model_var = tk.StringVar(value=MODEL_LABELS.get(self.settings.model, self.settings.model))
        self.model_box = ttk.Combobox(bottom, textvariable=self.model_var, values=list(self.model_ids), state="readonly",
                                      font=self.F["side"])
        self.model_box.pack(fill="x")
        self.model_box.bind("<<ComboboxSelected>>", self.on_model)

    def _build_center(self) -> None:
        c = self.center
        self.notices = tk.Frame(c, bg=C["bg"])
        self.notices.pack(fill="x")
        bottom = tk.Frame(c, bg=C["bg"])
        bottom.pack(side="bottom", fill="x", padx=self.px(24), pady=(6, 14))
        self.progress = ttk.Progressbar(c, mode="indeterminate", length=100)
        self.chat_frame = chat_frame = tk.Frame(c, bg=C["bg"])
        chat_frame.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(chat_frame, orient="vertical")
        self.chat = tk.Text(chat_frame, wrap="word", bg=C["bg"], fg=C["text"], relief="flat", bd=0, highlightthickness=0,
                            padx=self.px(28), pady=self.px(14), font=self.F["body"], cursor="arrow",
                            yscrollcommand=sb.set, state="disabled", spacing2=3, takefocus=0)
        sb.config(command=self.chat.yview)
        sb.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)
        self._chat_tags()

        box = tk.Frame(bottom, bg=C["bg"], highlightthickness=1, highlightbackground=C["line"],
                       highlightcolor=C["accent"])
        box.pack(fill="x")
        self.input = tk.Text(box, height=3, wrap="word", font=self.F["body"], relief="flat", bd=0, highlightthickness=0,
                             padx=12, pady=8, bg=C["bg"], fg=C["text"], insertbackground=C["text"])
        self.input.pack(side="left", fill="x", expand=True)
        self.ask_button = self.button(box, "Ask", self.submit, primary=True, width=7)
        self.ask_button.pack(side="right", padx=8, pady=8, anchor="s")
        self.input.bind("<Return>", self._on_return)
        self.input.bind("<FocusIn>", lambda e: box.configure(highlightbackground=C["accent"]))
        self.input.bind("<FocusOut>", lambda e: box.configure(highlightbackground=C["line"]))
        tk.Label(bottom, text="Enter to ask   ·   Shift+Enter for a new line   ·   Ctrl+N new chat",
                 bg=C["bg"], fg=C["muted"], font=self.F["small"], anchor="w").pack(fill="x", pady=(4, 0))
        self.input.focus_set()

    def _build_right(self) -> None:
        rt = self.right
        head = tk.Frame(rt, bg=C["bg"])
        head.pack(fill="x", padx=self.px(22), pady=(self.px(16), 6))
        self.viewer_title = tk.Label(head, text="Transcript", bg=C["bg"], fg=C["text"], font=self.F["h2"], anchor="w")
        self.viewer_title.pack(fill="x")
        self.viewer_sub = tk.Label(head, text="", bg=C["bg"], fg=C["muted"], font=self.F["small"], anchor="w")
        self.viewer_sub.pack(fill="x")
        tk.Frame(rt, bg=C["line"], height=1).pack(fill="x")
        self.viewer_note = tk.Label(rt, text="", anchor="w", justify="left", font=self.F["side"], padx=16, pady=8)
        rt.bind("<Configure>", lambda e: self.viewer_note.configure(wraplength=max(e.width - 50, 200)))
        self.viewer_frame = frame = tk.Frame(rt, bg=C["bg"])
        frame.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(frame, orient="vertical")
        self.viewer = tk.Text(frame, wrap="word", bg=C["bg"], fg=C["text"], relief="flat", bd=0, highlightthickness=0,
                              padx=self.px(22), pady=self.px(12), font=self.F["body"], cursor="arrow",
                              yscrollcommand=sb.set, state="disabled", spacing2=3, takefocus=0)
        sb.config(command=self.viewer.yview)
        sb.pack(side="right", fill="y")
        self.viewer.pack(side="left", fill="both", expand=True)
        v = self.viewer
        v.tag_configure("muted", foreground=C["muted"], font=self.F["body"])
        v.tag_configure("turn_label", font=self.F["small_bold"], foreground=C["accent"], spacing1=10)
        v.tag_configure("turn_text", font=self.F["body"], spacing3=2)
        v.tag_configure("focus", background=C["focus"])
        v.tag_configure("quote", background=C["quote"])
        v.tag_raise("quote")

    def _chat_tags(self) -> None:
        t, F = self.chat, self.F
        t.tag_configure("center", justify="center")
        t.tag_configure("h1", font=F["h1"], justify="center", spacing1=self.px(70), spacing3=6)
        t.tag_configure("sub", font=F["body"], foreground=C["muted"], justify="center", spacing3=18)
        t.tag_configure("you_label", font=F["small_bold"], foreground=C["accent"], spacing1=16, spacing3=2)
        t.tag_configure("you", font=F["body"], background=C["user_bg"], lmargin1=10, lmargin2=10, rmargin=10,
                        spacing1=5, spacing3=5)
        t.tag_configure("ans_label", font=F["small_bold"], foreground=C["muted"], spacing1=14, spacing3=2)
        t.tag_configure("p", font=F["body"], spacing1=2, spacing3=6)
        t.tag_configure("h", font=F["h3"], spacing1=8, spacing3=4)
        for level in range(4):
            t.tag_configure(f"b{level}", lmargin1=self.px(14 + 18 * level), lmargin2=self.px(32 + 18 * level))
        t.tag_configure("table", font=F["code"])
        t.tag_configure("bold", font=F["bold"])
        t.tag_configure("italic", font=F["italic"])
        t.tag_configure("code", font=F["code"], background=C["code_bg"])
        for status in ("ok", "weak", "bad"):
            t.tag_configure(f"cite_{status}", font=F["chip"], background=C[f"{status}_bg"], foreground=C[f"{status}_fg"])
        t.tag_configure("caption", font=F["small"], foreground=C["muted"], spacing1=4, spacing3=4)
        t.tag_configure("link", font=F["small"], foreground=C["accent"], underline=True)
        t.tag_configure("warn", font=F["side"], foreground=C["weak_fg"], background=C["weak_bg"], lmargin1=8, lmargin2=8,
                        spacing1=4, spacing3=4)
        t.tag_configure("error", font=F["side"], foreground=C["bad_fg"], background=C["bad_bg"], lmargin1=8,
                        lmargin2=8, spacing1=4, spacing3=4)
        t.tag_configure("thinking", font=F["italic"], foreground=C["muted"], spacing1=14)

    # ------------------------------------------------------------------ small updates

    def refresh_meetings(self) -> None:
        for child in self.meeting_box.winfo_children():
            child.destroy()
        for m in self.loaded.meetings:
            row = tk.Frame(self.meeting_box, bg=C["side"], cursor="hand2")
            row.pack(fill="x", pady=1)
            title = tk.Label(row, text=m.title, bg=C["side"], fg=C["text"], font=self.F["side_bold"], anchor="w")
            title.pack(fill="x", padx=8, pady=(5, 0))
            sub = tk.Label(row, text=f"{len(m.turns)} turns  ·  {m.word_count:,} words", bg=C["side"],
                           fg=C["muted"], font=self.F["small"], anchor="w")
            sub.pack(fill="x", padx=8, pady=(0, 5))
            parts = (row, title, sub)
            for w in parts:
                w.bind("<Button-1>", lambda e, m=m: self.show_meeting(m))
                w.bind("<Enter>", lambda e, p=parts: [x.configure(bg=C["hover"]) for x in p])
                w.bind("<Leave>", lambda e, p=parts: [x.configure(bg=C["side"]) for x in p])
        if not self.loaded.meetings:
            tk.Label(self.meeting_box, text="No transcripts found", bg=C["side"], fg=C["bad_fg"], font=self.F["small"],
                     anchor="w").pack(fill="x", padx=8)

    def refresh_notices(self) -> None:
        for child in self.notices.winfo_children():
            child.destroy()
        items: list[tuple[str, str, tuple[str, callable] | None]] = []
        s = self.settings
        if not self.setup_visible:  # the first-run checklist already says these two things
            if self.loaded.folder_missing or not self.loaded.meetings:
                items.append(("bad", f"No transcripts found in {s.transcripts_dir}. Export your meetings from Otter as "
                                     ".txt files and add them to that folder.", ("Add files…", self.add_files)))
            if not has_credentials():
                items.append(("weak", "No API key yet, so questions cannot be answered. You can still read the "
                                      "transcripts.", ("Open Settings", self.ask_for_key)))
        for name, reason in self.loaded.failed:
            items.append(("weak", f"Could not read {name}: {reason}", None))
        if self.env_note:
            items.append(("weak", self.env_note, None))
        if self.prompt_tokens[0] > s.retrieval_threshold_tokens and s.engine == "full":
            items.append(("weak", f"Your transcripts add up to about {self.prompt_tokens[0]:,} tokens, above the "
                                  f"{s.retrieval_threshold_tokens:,} full context is designed for. Every question "
                                  "sends all of them, so answers get slower and cost more. The RAG engine sends only "
                                  "the passages that match.", ("Switch to RAG", lambda: self.set_engine("rag"))))
        if self.tracker.capped:
            items.append(("bad", f"Session spending limit reached (${self.tracker.total:.2f} of "
                                 f"${s.max_session_cost_usd:.2f}). Raise it in Settings, or restart the app.",
                          ("Open Settings", self.open_settings)))
        spend = guardrails.spend_status(s)  # GR-COST-05
        for name, spent, limit, share, reached, later in (
                ("Daily", spend.today, spend.daily_limit, spend.daily_share, spend.daily_reached, "tomorrow"),
                ("Monthly", spend.month, spend.monthly_limit, spend.monthly_share, spend.monthly_reached, "next month")):
            if reached:
                items.append(("bad", f"{name} spending limit reached (${spent:.2f} of ${limit:.2f}). Questions resume "
                                     f"{later}, or raise the limit in Settings.",
                              ("Open Settings", lambda: self.open_settings("guardrails"))))
            elif limit > 0 and share >= 0.8:
                items.append(("weak", f"{share:.0%} of the {name.lower()} spending limit is used "
                                      f"(${spent:.2f} of ${limit:.2f}).", None))
        self.spend_blocked = spend.daily_reached or spend.monthly_reached
        for status, text, action in items:
            row = tk.Frame(self.notices, bg=C[f"{status}_bg"])
            row.pack(fill="x")
            label = tk.Label(row, text=text, bg=C[f"{status}_bg"], fg=C[f"{status}_fg"], font=self.F["side"],
                             anchor="w", justify="left", wraplength=self.px(560))
            label.pack(side="left", fill="x", expand=True, padx=14, pady=8)
            if action:
                b = tk.Button(row, text=action[0], command=action[1], relief="flat", bd=0, cursor="hand2",
                              bg=C[f"{status}_fg"], fg="#ffffff", font=self.F["side"], padx=10, pady=4)
                b.pack(side="right", padx=14)
            row.bind("<Configure>", lambda e, lb=label: lb.configure(wraplength=max(e.width - self.px(200), 200)))
        self._sync_input()

    def update_cost(self) -> None:
        self.cost_label.configure(text=f"${self.tracker.total:.4f}")
        n = self.tracker.calls
        self.cost_sub.configure(text=f"{n} question{'s' if n != 1 else ''} this session  ·  cap "
                                     f"${self.settings.max_session_cost_usd:.2f}")

    def _sync_input(self) -> None:
        """The box stays usable while an answer loads (you can type the next question); only Ask waits."""
        blocked = self.tracker.capped or self.spend_blocked
        enabled = not self.busy and not blocked
        self.ask_button.configure(state="normal" if enabled else "disabled",
                                  bg=C["accent"] if enabled else "#9ca3af")
        self.input.configure(state="disabled" if blocked else "normal")

    # ------------------------------------------------------------------ chat area

    def _put(self, text: str, *tags: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert("end", text, tags)
        self.chat.configure(state="disabled")

    def _clear_chat(self) -> None:
        with Writable(self.chat) as t:
            t.delete("1.0", "end")

    def show_welcome(self) -> None:
        """The empty-chat screen: example questions, or the two setup steps while the key or transcripts are missing."""
        self._clear_chat()
        key_ok, files_ok = has_credentials(), bool(self.loaded.meetings)
        self.setup_visible = not (key_ok and files_ok)
        if self.setup_visible:
            self._show_setup(key_ok, files_ok)
            return
        self._put("Ask about your meetings\n", "h1")
        self._put("Type a question below. Answers come only from your transcripts, with the exact source one click away.\n",
                  "sub")
        for example in (self.settings.examples or DEFAULT_EXAMPLES):
            b = tk.Button(self.chat, text=example, command=lambda q=example: self.ask(q), relief="flat", bd=0,
                          cursor="hand2", bg=C["user_bg"], fg=C["accent_dark"], activebackground=C["hover"],
                          font=self.F["body"], padx=14, pady=8)
            self.chat.configure(state="normal")
            first = self.chat.index("end-1c")
            self.chat.window_create("end", window=b, pady=4)
            self.chat.insert("end", "\n")
            self.chat.tag_add("center", first, "end-1c")
            self.chat.configure(state="disabled")

    def _show_setup(self, key_ok: bool, files_ok: bool) -> None:
        self._put("Welcome to MeetMiner\n", "h1")
        self._put("Two quick steps, then ask anything about your meetings.\n", "sub")
        folder = self.settings.transcripts_dir
        card = tk.Frame(self.chat, bg=C["bg"], width=self.px(620))
        self._step(card, 1, key_ok, "Add your Anthropic API key",
                   "MeetMiner uses Anthropic's Claude to write answers, so you need your own key. It is stored "
                   "encrypted on this PC and only ever sent to Anthropic.",
                   [("Open Settings", self.ask_for_key, True), ("Get a key", lambda: webbrowser.open(KEYS_URL), False)],
                   done_text="API key saved.")
        self._step(card, 2, files_ok, "Add your transcripts",
                   "In Otter, open a conversation, choose Export, then Text (.txt), and save the file in this folder:\n"
                   f"{folder}",
                   [("Add files…", self.add_files, True), ("Open folder", self.open_folder, False),
                    ("I've added them", self.reload, False)],
                   done_text=f"{len(self.loaded.meetings)} transcript{'s' if len(self.loaded.meetings) != 1 else ''} found.")
        self.chat.configure(state="normal")
        first = self.chat.index("end-1c")
        self.chat.window_create("end", window=card, pady=4)
        self.chat.insert("end", "\n")
        self.chat.tag_add("center", first, "end-1c")
        self.chat.configure(state="disabled")

    def _step(self, parent, number: int, done: bool, title: str, text: str, buttons, done_text: str) -> None:
        """One card of the first-run checklist."""
        row = tk.Frame(parent, bg=C["side"], highlightthickness=1, highlightbackground=C["line"])
        row.pack(fill="x", pady=6)
        badge = tk.Label(row, text="✓" if done else str(number), width=3, font=self.F["h3"],
                         bg=C["ok_fg"] if done else C["accent"], fg="#ffffff")
        badge.pack(side="left", fill="y")
        body = tk.Frame(row, bg=C["side"])
        body.pack(side="left", fill="x", expand=True, padx=14, pady=10)
        tk.Label(body, text=title, bg=C["side"], fg=C["text"], font=self.F["bold"], anchor="w").pack(fill="x")
        tk.Label(body, text=done_text if done else text, bg=C["side"], fg=C["ok_fg"] if done else C["muted"],
                 font=self.F["side"], anchor="w", justify="left", wraplength=self.px(500)).pack(fill="x", pady=(2, 8))
        bar = tk.Frame(body, bg=C["side"])
        bar.pack(fill="x")
        for label, command, primary in buttons:
            if done and primary:
                label = {"Open Settings": "Change key", "Add files…": "Add more files…"}.get(label, label)
            self.button(bar, label, command, primary=primary and not done).pack(side="left", padx=(0, 8))

    def new_chat(self) -> None:
        if self.busy:
            return
        self.history.clear()
        self.show_welcome()
        self.refresh_notices()
        self.input.focus_set()

    def _on_return(self, event) -> str | None:
        if event.state & 0x1:  # Shift: let Tk insert a new line
            return None
        self.submit()
        return "break"

    def submit(self) -> None:
        text = self.input.get("1.0", "end").strip()
        if text and self.ask(text):
            self.input.delete("1.0", "end")

    def ask(self, question: str) -> bool:
        question = question.strip()
        spend = guardrails.spend_status(self.settings)  # the day may have changed, or the log grown, since the last look
        self.spend_blocked = spend.daily_reached or spend.monthly_reached
        if not question or self.busy or self.tracker.capped or self.spend_blocked:
            if self.spend_blocked:
                self.refresh_notices()
            return False
        if not has_credentials():
            self.ask_for_key()
            return False
        if not self.loaded.meetings:  # nothing to search yet: show the steps again instead of failing
            if not self.history:
                self.show_welcome()
            self.refresh_notices()
            return False
        if not self.history:
            self._clear_chat()
            self.setup_visible = False
        self.chat.mark_set("last_question", "end-1c")
        self.chat.mark_gravity("last_question", "left")
        self._put("You\n", "you_label")
        self._put(question + "\n", "you")
        self._put("Reading the transcripts…\n", "thinking")
        self.chat.mark_set("think_start", "end-1c linestart -1l")
        self.chat.see("end")
        self.busy = True
        self._sync_input()
        self.progress.pack(side="bottom", fill="x", before=self.chat_frame)
        self.progress.start(12)
        api_history = [(h["q"], h["ans"].text) for h in self.history if h["ans"].text and not h["ans"].error]
        threading.Thread(target=self._worker, args=(question, api_history), daemon=True).start()
        return True

    def _worker(self, question: str, api_history: list[tuple[str, str]]) -> None:
        try:
            ans = self.qa.ask(question, api_history)
        except Exception as exc:  # a crashed worker must still answer, or the window would wait forever
            ans = Answer(question=question, error=f"Something went wrong: {exc}")
        self.results.put(("answer", question, ans))

    def _count_tokens(self) -> None:
        self.results.put(("tokens", self.qa.count_corpus_tokens()))

    def _poll(self) -> None:
        try:
            while True:
                item = self.results.get_nowait()
                if item[0] == "answer":
                    self._finish(item[1], item[2])
                elif item[0] == "tokens":
                    self.prompt_tokens = item[1]
                    n, how = item[1]
                    self.tokens_label.configure(
                        text=f"Transcripts: {n:,} tokens" + ("" if how == "measured" else " (est.)")
                        if self.loaded.meetings else "Transcripts: none yet")
                    self.refresh_notices()
        except queue.Empty:
            pass
        self._poll_id = self.root.after(100, self._poll)

    def close(self) -> None:
        if self._poll_id:
            self.root.after_cancel(self._poll_id)
        self.root.destroy()

    def _finish(self, question: str, ans: Answer) -> None:
        self.progress.stop()
        self.progress.pack_forget()
        with Writable(self.chat) as t:  # drop the "Reading the transcripts..." line
            start = t.index("think_start") if "think_start" in t.mark_names() else None
            if start:
                t.delete(start, "end-1c")
        self.busy = False
        self.history.append({"q": question, "ans": ans})
        self.answer_count += 1
        self._render_answer(ans, self.answer_count)
        self.update_cost()
        self.refresh_notices()
        self.chat.update_idletasks()
        self.chat.yview("last_question")  # show the question and the start of its answer, read downwards
        if not self.tracker.capped:
            self.input.focus_set()

    # ------------------------------------------------------------------ rendering an answer

    def _render_answer(self, ans: Answer, answer_id: int) -> None:
        self._put("Answer\n", "ans_label")
        if ans.error:
            self._put(ans.error + "\n", "error")
        cites = ans.check.citations if ans.check else []
        counter = 0
        for line in ans.text.split("\n") if ans.text else []:
            kind, text, level, marker = block_kind(line)
            if kind == "blank":
                continue
            base: list[str] = ["p"]
            prefix = ""
            if kind == "heading":
                base = ["h"]
            elif kind == "bullet":
                base, prefix = ["p", f"b{level}"], "•  "
            elif kind == "number":
                base, prefix = ["p", f"b{level}"], f"{marker}.  "
            elif kind == "table":
                base = ["table"]
            if prefix:
                self._put(prefix, *base)
            counter = self._inline(text, base, cites, counter, ans, answer_id)
            self._put("\n", *base)
        report = ans.check
        if report and report.citations and not report.clean:
            self._put("Some citations or quotes could not be fully verified. Click the orange or red labels to see "
                      "where the words really are.\n", "warn")
        elif ans.text and not (report and report.citations) and "not found in the transcripts" not in ans.text.lower():
            self._put("No citations in this answer, so it cannot be verified.\n", "warn")
        if report and report.numbers_flagged:  # GR-INT-04
            self._put("Check these numbers against the transcript:\n", "warn")
            for line in guardrails.describe_numbers(report.numbers):
                self._put(f"   •  {line}\n", "warn")
        if ans.tokens["output"] or ans.tokens["input"] or ans.tokens["cache_read"] or ans.retrieved is not None:
            self._put(caption_for(ans) + "   ", "caption")
            tag = f"copy{answer_id}"
            self._put("Copy answer", "link", tag)
            self._put("\n", "caption")
            self.chat.tag_bind(tag, "<Button-1>", lambda e, a=ans: self.copy(a.text))
            self._hand(tag)

    def _inline(self, text: str, base: list[str], cites, counter: int, ans: Answer, answer_id: int) -> int:
        pos = 0
        for m in CITE_RE.finditer(text):
            self._runs(text[pos:m.start()], base)
            if counter < len(cites):
                self._chip(cites[counter], counter, ans, answer_id, base)
            else:
                self._put(m.group(0), *base)
            counter += 1
            pos = m.end()
        self._runs(text[pos:], base)
        return counter

    def _runs(self, text: str, base: list[str]) -> None:
        for piece, style in inline_runs(text):
            self._put(piece, *base, *([style] if style else []))

    def _chip(self, chk, index: int, ans: Answer, answer_id: int, base: list[str]) -> None:
        """A coloured, clickable citation label. It carries the line's own tags so margins stay right."""
        tag = f"cite{answer_id}_{index}"
        nb = " "  # non-breaking spaces keep the label in one piece when the line wraps
        label = f"{nb}{chk.title}{nb}{nb}·{nb}{nb}{chk.speaker}{nb}{nb}·{nb}{nb}{chk.ts}{nb}{nb}{CHIP[chk.status]}{nb}"
        self._put(" ", *base)
        self._put(label.replace(" ", nb), *base, f"cite_{chk.status}", tag)
        self._put(" ", *base)
        self.chat.tag_bind(tag, "<Button-1>", lambda e, a=ans, i=index: self.show_citation(a, i))
        self._hand(tag)

    def _hand(self, tag: str) -> None:
        self.chat.tag_bind(tag, "<Enter>", lambda e: self.chat.configure(cursor="hand2"))
        self.chat.tag_bind(tag, "<Leave>", lambda e: self.chat.configure(cursor="arrow"))

    def copy(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)

    # ------------------------------------------------------------------ the transcript reader (right side)

    def _set_note(self, notes: list[tuple[str, str]] | None) -> None:
        """A coloured message above the transcript (it must stay visible while the transcript scrolls)."""
        if not notes:
            self.viewer_note.pack_forget()
            return
        worst = "bad" if any(s == "bad" for _, s in notes) else "weak" if any(s == "weak" for _, s in notes) else "ok"
        self.viewer_note.configure(text="\n".join(t for t, _ in notes), bg=C[f"{worst}_bg"], fg=C[f"{worst}_fg"])
        self.viewer_note.pack(fill="x", before=self.viewer_frame)

    def show_placeholder(self) -> None:
        self.viewer_title.configure(text="Transcript")
        self.viewer_sub.configure(text="")
        self._set_note(None)
        with Writable(self.viewer) as v:
            v.delete("1.0", "end")
            v.insert("end", "Click a coloured citation in an answer, or a meeting on the left, to read the "
                            "transcript here. The passage an answer relies on is highlighted.", ("muted",))

    def show_citation(self, ans: Answer, index: int) -> None:
        chk = ans.check.citations[index]
        if chk.meeting is None or chk.turn_idx is None:
            self.viewer_title.configure(text=chk.title)
            self.viewer_sub.configure(text="")
            self._set_note([("✕  This passage could not be found in the transcripts.", "bad")]
                           + [("!  " + p, "bad") for p in chk.problems])
            with Writable(self.viewer) as v:
                v.delete("1.0", "end")
            return
        focus = chk.found_idx if chk.found_idx is not None else chk.turn_idx
        quotes = [q.text for q in chk.quotes if q.status in ("near", "elsewhere")]
        notes: list[tuple[str, str]] = []
        if chk.status == "ok":
            notes.append(("✓  " + ("The quoted words are in the highlighted turn." if quotes
                                       else "This is the turn the answer points to."), "ok"))
        for problem in chk.problems:
            notes.append(("!  " + problem, "weak" if chk.status != "bad" else "bad"))
        if focus != chk.turn_idx:
            notes.append((f"The quoted words are in the highlighted turn ({chk.meeting.turns[focus].start}), "
                          f"not at {chk.ts}.", "weak"))
        self.show_meeting(chk.meeting, focus, quotes, notes)

    def show_meeting(self, meeting: Meeting, focus: int | None = None, quotes: list[str] | None = None,
                     notes: list[tuple[str, str]] | None = None) -> None:
        self.viewer_title.configure(text=meeting.title)
        self.viewer_sub.configure(text=f"{len(meeting.turns)} turns  ·  {meeting.word_count:,} words  ·  "
                                       f"{meeting.path.name}")
        first_quote = focus_start = None
        self._set_note(notes)
        with Writable(self.viewer) as v:
            v.delete("1.0", "end")
            for t in meeting.turns:
                start = v.index("end-1c")
                v.insert("end", f"{t.label}  ·  {t.start}\n", ("turn_label",))
                body = v.index("end-1c")
                v.insert("end", t.text + "\n", ("turn_text",))
                end = v.index("end-1c")
                v.insert("end", "\n", ("turn_text",))
                if t.idx == focus:
                    v.tag_add("focus", start, end)
                    focus_start = start
                    for quote in quotes or []:
                        for a, b in quote_spans(t.text, quote):
                            v.tag_add("quote", f"{body}+{a}c", f"{body}+{b}c")
                            first_quote = first_quote or f"{body}+{a}c"
        self.viewer.update_idletasks()
        target = first_quote or focus_start
        if target:
            self.viewer.yview(target)
            self.viewer.yview_scroll(-2, "units")
        else:
            self.viewer.yview_moveto(0)

    # ------------------------------------------------------------------ actions

    def on_model(self, _event=None) -> None:
        model = self.model_ids.get(self.model_var.get())
        if not model or model == self.settings.model:
            return
        if not self.confirm_model_switch(model):  # GR-COST-06: put the box back to the model still in use
            self.model_var.set(MODEL_LABELS.get(self.settings.model, self.settings.model))
            return
        self.settings = with_overrides(self.base_settings, model=model)
        self.tracker.settings = self.settings  # keeps the running total, logs the right model
        self.qa = MeetingQA(self.settings, self.loaded.meetings, self.tracker, client=self.qa._client)
        threading.Thread(target=self._count_tokens, daemon=True).start()
        self.update_cost()
        self.input.focus_set()

    def _style_engine(self) -> None:
        """The chosen half of the engine switch is filled in, and the line under it says what that engine sends."""
        for key, b in self.engine_buttons.items():
            on = key == self.settings.engine
            b.configure(bg=C["accent"] if on else C["bg"], fg="#ffffff" if on else C["text"],
                        activebackground=C["accent_dark"] if on else C["hover"],
                        activeforeground="#ffffff" if on else C["text"])
        self.engine_note.configure(text="Sends all transcripts with each question" if self.settings.engine == "full"
                                   else "Sends only the passages that match")

    def set_engine(self, engine: str) -> None:
        """Switch between full context and RAG. Remembered for next time. Not while an answer is on its way, so the
        answer is logged under the engine that actually produced it."""
        if engine == self.settings.engine or engine not in ENGINES or self.busy:
            return
        storage.update_user_settings(engine=engine)
        self._replace_settings(engine=engine)
        self._style_engine()
        self.refresh_notices()

    def confirm_model_switch(self, new_model: str) -> bool:
        """GR-COST-06: before using a model that costs more per question, say how much more, and ask."""
        s = self.settings
        if not s.confirm_model_switch:
            return True
        tokens = self.prompt_tokens[0] if self.loaded.meetings else 0
        cmp = guardrails.compare_models(s.price_table, s.model, new_model, s.cache_ttl, tokens)
        if cmp is None or not cmp.more_expensive:
            return True

        def name(model: str) -> str:
            return MODEL_LABELS.get(model, model).split("  ·")[0]

        times = f"{cmp.multiple:.0f}" if cmp.multiple >= 10 else f"{cmp.multiple:.1f}"
        idle = "five idle minutes" if s.cache_ttl == "5m" else "an idle hour"
        size = f"about {tokens:,} tokens" if tokens else "typical size"
        return messagebox.askyesno(
            APP_NAME,
            f"{name(new_model)} costs about {times}× more per question than {name(s.model)}.\n\n"
            f"For your transcripts ({size}) a typical question would cost about ${cmp.new_warm:.4f} instead of "
            f"${cmp.old_warm:.4f}. The first question after {idle} costs about ${cmp.new_cold:.4f} instead of "
            f"${cmp.old_cold:.4f}.\n\nSwitch to {name(new_model)}?",
            icon="warning", default=messagebox.NO, parent=self.root)

    def reload(self) -> None:
        """Read the transcripts folder again (after files were added, or the folder changed)."""
        self.loaded = load_meetings(self.settings.transcripts_dir, self.settings.meetings_meta)
        self.qa.meetings = self.loaded.meetings
        self._folder_sig = folder_signature(self.settings.transcripts_dir)
        if not self.history:  # the two setup steps tick themselves off, then turn into the normal welcome screen
            self.show_welcome()
        self.refresh_meetings()
        self.refresh_notices()
        threading.Thread(target=self._count_tokens, daemon=True).start()
        self.show_placeholder()

    def _on_focus(self, event) -> None:
        """Coming back from Explorer after saving transcripts: pick them up without a click on Reload."""
        if event.widget is self.root and folder_signature(self.settings.transcripts_dir) != self._folder_sig:
            self.reload()

    def open_folder(self) -> None:
        folder = self.settings.transcripts_dir
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)  # opens Explorer
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not open {folder}:\n{exc}", parent=self.root)

    def add_files(self, paths: list[str] | None = None) -> int:
        """Copy transcript .txt files into the transcripts folder, then reload. Returns how many were copied."""
        if paths is None:
            paths = filedialog.askopenfilenames(
                parent=self.root, title="Choose your Otter transcript files",
                filetypes=[("Otter text exports", "*.txt"), ("All files", "*.*")])
        if not paths:
            return 0
        folder = self.settings.transcripts_dir
        copied = 0
        try:
            folder.mkdir(parents=True, exist_ok=True)
            for p in paths:
                shutil.copy2(p, unique_name(folder, Path(p).name))
                copied += 1
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"Could not copy the files into {folder}:\n{exc}", parent=self.root)
        self.reload()
        return copied

    def _replace_settings(self, **changes) -> None:
        """Apply a changed setting everywhere the running app holds a copy of the settings."""
        self.base_settings = replace(self.base_settings, **changes)
        self.settings = replace(self.settings, **changes)
        self.tracker.settings = self.settings
        self.qa.settings = self.settings

    def set_transcripts_folder(self, folder: Path) -> None:
        storage.update_user_settings(transcripts_dir=str(folder))
        self._replace_settings(transcripts_dir=folder)
        self.reload()

    def set_spending_limit(self, dollars: float) -> None:
        storage.update_user_settings(max_session_cost_usd=dollars)
        self._replace_settings(max_session_cost_usd=dollars)
        self.update_cost()
        self.refresh_notices()

    def set_period_limit(self, name: str, dollars: float) -> None:
        """name is "max_daily_cost_usd" or "max_monthly_cost_usd"; 0 means no limit."""
        storage.update_user_settings(**{name: dollars})
        self._replace_settings(**{name: dollars})
        self.refresh_notices()

    def set_confirm_model_switch(self, ask_first: bool) -> None:
        storage.update_user_settings(confirm_model_switch=ask_first)
        self._replace_settings(confirm_model_switch=ask_first)

    def on_key_changed(self) -> None:
        self.qa.reset_client()  # the next call uses the new key
        if not self.history:
            self.show_welcome()
        self.refresh_notices()
        threading.Thread(target=self._count_tokens, daemon=True).start()

    def open_settings(self, focus: str | None = None) -> None:
        SettingsDialog(self, focus)

    def ask_for_key(self) -> None:
        self.open_settings("key")


class SettingsDialog(tk.Toplevel):
    """Three pages: General (API key, transcripts), Guardrails (spending limits and warnings) and Reports (your usage and
    the release scorecard). Each setting is saved as soon as you press its button."""

    def __init__(self, app: App, focus: str | None = None):
        super().__init__(app.root)
        self.app = app
        F = app.F
        self.title(f"{APP_NAME} settings")
        self.configure(bg=C["bg"], padx=app.px(22), pady=app.px(16))
        self.transient(app.root)
        self.results: queue.Queue = queue.Queue()
        width = app.px(680)
        self.minsize(app.px(760), app.px(520))

        tk.Label(self, text="Settings", bg=C["bg"], font=F["h2"], anchor="w").pack(fill="x")
        closerow = tk.Frame(self, bg=C["bg"])
        closerow.pack(side="bottom", fill="x", pady=(12, 0))
        app.button(closerow, "Close", self.destroy, primary=True).pack(side="right")
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, pady=(8, 0))
        general = tk.Frame(self.tabs, bg=C["bg"], padx=app.px(14), pady=app.px(4))
        guard = tk.Frame(self.tabs, bg=C["bg"], padx=app.px(14), pady=app.px(4))
        self.reports_panel = ReportsPanel(self.tabs, app, C, engine="full")
        self.rag_reports_panel = ReportsPanel(self.tabs, app, C, engine="rag")
        self.tabs.add(general, text="  General  ")
        self.tabs.add(guard, text="  Guardrails  ")
        self.tabs.add(self.reports_panel, text="  Full-context reports  ")
        self.tabs.add(self.rag_reports_panel, text="  RAG reports  ")
        page = {"reports": self.reports_panel, "rag-reports": self.rag_reports_panel, "guardrails": guard}.get(focus or "")
        if page is not None:
            self.tabs.select(page)

        def heading(parent, text):
            tk.Label(parent, text=text, bg=C["bg"], fg=C["text"], font=F["bold"], anchor="w").pack(fill="x", pady=(14, 2))

        def note(parent, text, fg=C["muted"]):
            label = tk.Label(parent, text=text, bg=C["bg"], fg=fg, font=F["small"], anchor="w", justify="left",
                             wraplength=width)
            label.pack(fill="x")
            return label

        # --- General: API key
        heading(general, "Anthropic API key")
        self.key_status = note(general, "")
        keyrow = tk.Frame(general, bg=C["bg"])
        keyrow.pack(fill="x", pady=(6, 0))
        self.key_var = tk.StringVar()
        self.key_entry = tk.Entry(keyrow, textvariable=self.key_var, show="•", font=F["body"], relief="solid", bd=1)
        self.key_entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.show_var = tk.BooleanVar(value=False)
        tk.Checkbutton(keyrow, text="Show", variable=self.show_var, bg=C["bg"], font=F["small"],
                       command=lambda: self.key_entry.configure(show="" if self.show_var.get() else "•")
                       ).pack(side="left", padx=(8, 0))
        keybuttons = tk.Frame(general, bg=C["bg"])
        keybuttons.pack(fill="x", pady=(8, 0))
        app.button(keybuttons, "Save key", self.save_key, primary=True).pack(side="left")
        app.button(keybuttons, "Test key", self.test_key).pack(side="left", padx=6)
        app.button(keybuttons, "Remove key", self.remove_key).pack(side="left")
        app.button(keybuttons, "Get a key", lambda: webbrowser.open(KEYS_URL)).pack(side="right")
        self.key_message = note(general, "")

        # --- General: transcripts
        heading(general, "Transcripts folder")
        self.folder_label = note(general, str(app.settings.transcripts_dir), fg=C["text"])
        folderbuttons = tk.Frame(general, bg=C["bg"])
        folderbuttons.pack(fill="x", pady=(6, 0))
        app.button(folderbuttons, "Add files…", self.add_files).pack(side="left")
        app.button(folderbuttons, "Open folder", app.open_folder).pack(side="left", padx=6)
        app.button(folderbuttons, "Change folder…", self.change_folder).pack(side="left")
        note(general, "Put your Otter exports (.txt) here. Choose Export, then Text (.txt), in Otter.")

        # --- General: privacy
        heading(general, "Privacy")
        note(general, "Your question, the earlier questions in the chat, and the text of your transcripts are sent to "
                      "Anthropic to write each answer. Nothing else leaves this computer. Your key is stored encrypted for "
                      f"your Windows account. {APP_NAME} {__version__} is not affiliated with Otter.ai or Anthropic.")

        # --- Guardrails: spending limits
        s = app.settings
        heading(guard, "Spending limits (US dollars)")
        note(guard, "MeetMiner stops asking questions when a limit is reached. Type 0 for no limit.")
        self.limit_var = tk.StringVar(value=f"{s.max_session_cost_usd:.2f}")
        self.daily_var = tk.StringVar(value=f"{s.max_daily_cost_usd:.2f}")
        self.monthly_var = tk.StringVar(value=f"{s.max_monthly_cost_usd:.2f}")
        for label, var, command, hint in (
                ("Per session", self.limit_var, self.save_limit, "one run of the app; starts again at zero when you restart it"),
                ("Per day", self.daily_var, lambda: self.save_period("max_daily_cost_usd", self.daily_var),
                 "counted from your usage log, so it still applies after a restart"),
                ("Per month", self.monthly_var, lambda: self.save_period("max_monthly_cost_usd", self.monthly_var),
                 "the calendar month, counted the same way")):
            row = tk.Frame(guard, bg=C["bg"])
            row.pack(fill="x", pady=(8, 0))
            tk.Label(row, text=label, width=12, anchor="w", bg=C["bg"], fg=C["text"], font=F["body"]).pack(side="left")
            tk.Entry(row, textvariable=var, width=8, font=F["body"], relief="solid", bd=1).pack(side="left", ipady=3)
            app.button(row, "Save", command).pack(side="left", padx=8)
            tk.Label(row, text=hint, anchor="w", bg=C["bg"], fg=C["muted"], font=F["small"]).pack(side="left")

        heading(guard, "Switching to a more expensive model")
        self.confirm_var = tk.BooleanVar(value=s.confirm_model_switch)
        tk.Checkbutton(guard, text="Ask me first, and show how much more each question would cost",
                       variable=self.confirm_var, command=self.save_confirm, bg=C["bg"], font=F["body"], anchor="w"
                       ).pack(fill="x")

        heading(guard, "Protections in this version")
        for gid, name, state in (
                ("GR-COST-05", "Daily and monthly spending limits", "on"),
                ("GR-COST-06", "Confirmation before a more expensive model", "on" if s.confirm_model_switch else "off"),
                ("GR-INT-04", "Numbers and dates checked against the turn they are cited to", "on"),
                ("GR-REL-03", "A request that gets no answer is given up after "
                              f"{s.request_timeout_seconds:g} seconds (up to {s.max_retries} retries)", "on"),
                ("GR-RAG-01", "RAG: when no passage matches, nothing is sent and nothing is spent", "on")):
            row = tk.Frame(guard, bg=C["bg"])
            row.pack(fill="x", pady=(3, 0))
            tk.Label(row, text=gid, width=11, anchor="w", bg=C["bg"], fg=C["muted"], font=F["small_bold"]).pack(side="left")
            tk.Label(row, text=name, anchor="w", bg=C["bg"], fg=C["text"], font=F["small"]).pack(side="left")
            tk.Label(row, text=state, bg=C["ok_bg"] if state == "on" else C["weak_bg"],
                     fg=C["ok_fg"] if state == "on" else C["weak_fg"], font=F["small_bold"], padx=6).pack(side="right")
        note(guard, "The timeout and retries are set in config/default.yaml. Every guardrail has a spec in the repository's "
                    "guardrails folder.")

        self.refresh_key_status()
        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(app.px(820), int(sw * 0.94)), min(app.px(760), int(sh * 0.9))
        x = max(0, min(app.root.winfo_rootx() + app.px(90), sw - w - app.px(10)))
        y = max(0, min(app.root.winfo_rooty() + app.px(20), sh - h - app.px(50)))
        self.geometry(f"{w}x{h}+{x}+{y}")
        (self.key_entry if focus == "key" or not has_credentials() else closerow).focus_set()
        self.bind("<Escape>", lambda e: self.destroy())
        self.key_entry.bind("<Return>", lambda e: self.save_key())
        self.after(100, self._poll)
        self._grab()

    def destroy(self) -> None:
        super().destroy()
        # Tk variables must be freed on the main thread: if the garbage collector finalises them later inside a worker
        # thread (an answer being fetched), each one stalls that thread for about a second before failing.
        for name in ("key_var", "show_var", "limit_var", "daily_var", "monthly_var", "confirm_var"):
            self.__dict__.pop(name, None)
        gc.collect()

    def _grab(self) -> None:
        """Make the window modal (the main window waits). Harmless if the window is not on screen yet."""
        try:
            self.grab_set()
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ key

    def refresh_key_status(self) -> None:
        saved = storage.get_api_key()
        if saved:
            self.key_status.configure(text=f"A key is saved on this PC ({storage.key_hint(saved)}). Paste a new one to replace it.",
                                      fg=C["ok_fg"])
        elif os.environ.get("ANTHROPIC_API_KEY"):
            self.key_status.configure(text="Using the ANTHROPIC_API_KEY environment variable. You can also save a key here.",
                                      fg=C["ok_fg"])
        else:
            self.key_status.configure(text="No key yet. Paste your Anthropic API key below.", fg=C["weak_fg"])

    def say(self, text: str, status: str = "muted") -> None:
        colour = C["muted"] if status == "muted" else C[f"{status}_fg"]
        self.key_message.configure(text=text, fg=colour)

    def save_key(self) -> None:
        key = self.key_var.get().strip()
        if not key:
            self.say("Paste your key into the box first.", "weak")
            return
        if not key.startswith("sk-") and not messagebox.askyesno(
                APP_NAME, "That does not look like an Anthropic key (they start with sk-ant-). Save it anyway?", parent=self):
            return
        storage.set_api_key(key)
        self.key_var.set("")
        self.refresh_key_status()
        self.say("Key saved.", "ok")
        self.app.on_key_changed()

    def remove_key(self) -> None:
        if storage.get_api_key() and messagebox.askyesno(APP_NAME, "Remove the saved API key from this PC?", parent=self):
            storage.set_api_key(None)
            self.refresh_key_status()
            self.say("Key removed.", "ok")
            self.app.on_key_changed()

    def test_key(self) -> None:
        key = self.key_var.get().strip() or storage.get_api_key() or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            self.say("There is no key to test yet.", "weak")
            return
        self.say("Testing the key…")
        model = self.app.settings.model
        threading.Thread(target=self._test_worker, args=(key, model), daemon=True).start()

    def _test_worker(self, key: str, model: str) -> None:
        try:  # the token-counting call is free
            anthropic.Anthropic(api_key=key, timeout=20.0, max_retries=0).messages.count_tokens(
                model=model, messages=[{"role": "user", "content": "hi"}])
            self.results.put(("Anthropic accepted this key.", "ok"))
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            self.results.put(("Anthropic rejected this key. Check that you copied all of it.", "bad"))
        except anthropic.APIConnectionError:
            self.results.put(("Could not reach Anthropic. Check your internet connection.", "weak"))
        except anthropic.AnthropicError as exc:
            self.results.put((f"Anthropic answered: {getattr(exc, 'message', exc)}", "weak"))

    def _poll(self) -> None:
        if not self.winfo_exists():
            return
        try:
            while True:
                self.say(*self.results.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll)

    # ------------------------------------------------------------------ transcripts and limit

    def add_files(self) -> None:
        self.grab_release()
        self.app.add_files()
        self._grab()

    def change_folder(self) -> None:
        self.grab_release()
        chosen = filedialog.askdirectory(parent=self, title="Choose the folder with your transcripts",
                                         initialdir=str(self.app.settings.transcripts_dir))
        self._grab()
        if chosen:
            self.app.set_transcripts_folder(Path(chosen))
            self.folder_label.configure(text=str(self.app.settings.transcripts_dir))

    def save_limit(self) -> None:
        try:
            dollars = float(self.limit_var.get().replace("$", "").strip())
            if dollars <= 0:
                raise ValueError
        except ValueError:
            messagebox.showinfo(APP_NAME, "Enter an amount in dollars, for example 2.00.", parent=self)
            return
        self.app.set_spending_limit(dollars)
        self.limit_var.set(f"{dollars:.2f}")
        self.refresh_reports()

    def refresh_reports(self) -> None:
        self.reports_panel.refresh()
        self.rag_reports_panel.refresh()

    def save_period(self, name: str, var: tk.StringVar) -> None:
        """The daily or monthly limit (GR-COST-05). Unlike the session limit, 0 is allowed and means no limit."""
        try:
            dollars = float(var.get().replace("$", "").strip())
            if dollars < 0:
                raise ValueError
        except ValueError:
            messagebox.showinfo(APP_NAME, "Enter an amount in dollars, for example 5.00 (0 means no limit).", parent=self)
            return
        self.app.set_period_limit(name, dollars)
        var.set(f"{dollars:.2f}")
        self.refresh_reports()

    def save_confirm(self) -> None:
        self.app.set_confirm_model_switch(self.confirm_var.get())


# --------------------------------------------------------------------------- start-up


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MeetMiner desktop app")
    parser.add_argument("--ask", help="ask this question as soon as the window opens")
    parser.add_argument("--uninstall", action="store_true", help="remove the installed app (used by Windows Settings)")
    parser.add_argument("--sandbox", help=argparse.SUPPRESS)  # tests: keep shortcuts and registry entries in a folder
    parser.add_argument("--silent", action="store_true", help=argparse.SUPPRESS)  # tests: uninstall without a window
    args = parser.parse_args(argv)
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on high-resolution screens
        except (AttributeError, OSError):
            pass
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MeetMiner.Desktop")  # own taskbar icon
        except (AttributeError, OSError):
            pass
    if args.uninstall:
        from . import winsetup
        return winsetup.uninstall_main(Path(args.sandbox) if args.sandbox else None, silent=args.silent)
    env_note = load_env()
    try:
        settings = load_settings()
    except ConfigError as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_NAME, f"The settings need a fix:\n\n{exc}")
        return 2
    if settings.transcripts_dir == storage.default_transcripts_dir():
        try:  # the app's own folder: make sure "Open folder" has something to open
            settings.transcripts_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    loaded = load_meetings(settings.transcripts_dir, settings.meetings_meta)
    root = tk.Tk()
    App(root, settings, loaded, env_note, args.ask)
    root.mainloop()
    return 0


def run(argv: list[str] | None = None) -> int:
    """main(), but a crash is written to a log and shown in a message box (the installed app has no console)."""
    try:
        return main(argv)
    except Exception:
        log = storage.data_dir() / "desktop-error.log"
        log.write_text(f"{datetime.now().isoformat(timespec='seconds')}\n{traceback.format_exc()}", encoding="utf-8")
        try:
            messagebox.showerror(APP_NAME, f"The app hit a problem and has to close.\nDetails: {log}")
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(run())
