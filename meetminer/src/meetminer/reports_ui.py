"""The two Reports pages of the Settings window, one per engine (full context and RAG): spending, your own usage with
that engine, and that engine's release scorecard."""
from __future__ import annotations

import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import guardrails, reports, storage, usagelog

DECISION_STYLE = {"SHIP": "ok", "REVIEW": "weak", "HOLD": "bad"}
ENGINE_NAMES = {"full": "Full context", "rag": "RAG"}
RATING_LABEL = {"green": "good", "amber": "watch", "red": "fix", "unmeasured": "not measured", "info": "for information"}
RATING_STYLE = {"green": "ok", "amber": "weak", "red": "bad", "unmeasured": "muted", "info": "muted"}


def seconds_text(value: float | None) -> str:
    return "–" if value is None else f"{value:.1f} s"


def money_text(value: float) -> str:
    return f"${value:.4f}" if value < 1 else f"${value:.2f}"


class ReportsPanel(tk.Frame):
    """Scrolls, because the scorecard has about twenty rows. Everything is rebuilt by refresh()."""

    def __init__(self, parent, app, colors: dict, engine: str = "full"):
        super().__init__(parent, bg=colors["bg"])
        self.app, self.C, self.engine = app, colors, engine
        self.engine_name = ENGINE_NAMES.get(engine, engine)
        self.opened: Path | None = None  # a scorecard the user chose with "Open report file"
        self.card_text = ""  # what "Copy summary" copies

        style = ttk.Style(self)
        style.configure("Meet.Treeview", rowheight=app.px(24), font=app.F["small"], borderwidth=0)
        style.configure("Meet.Treeview.Heading", font=app.F["small_bold"])

        self.canvas = tk.Canvas(self, bg=colors["bg"], highlightthickness=0)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = tk.Frame(self.canvas, bg=colors["bg"])
        window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(window, width=e.width))
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        # One mouse-wheel handler for the whole app, pointed at whichever Reports page is on screen (a handler per page
        # would pile up, because bind_all bindings cannot be removed one by one).
        self.bind("<Map>", lambda e: setattr(app, "wheel_target", self))
        if not getattr(app, "wheel_bound", False):
            app.root.bind_all("<MouseWheel>", lambda e: self._wheel(app, e), add="+")
            app.wheel_bound = True
        self.refresh()

    @staticmethod
    def _wheel(app, event) -> None:
        target = getattr(app, "wheel_target", None)
        try:
            if target is not None and target.winfo_exists() and target.winfo_ismapped():
                target.canvas.yview_scroll(int(-event.delta / 120), "units")
        except tk.TclError:
            pass

    # ------------------------------------------------------------------ building blocks

    def heading(self, text: str, sub: str = "") -> None:
        tk.Label(self.body, text=text, bg=self.C["bg"], fg=self.C["text"], font=self.app.F["bold"], anchor="w").pack(
            fill="x", pady=(14, 0))
        if sub:
            self.note(sub)

    def note(self, text: str, fg: str | None = None) -> tk.Label:
        label = tk.Label(self.body, text=text, bg=self.C["bg"], fg=fg or self.C["muted"], font=self.app.F["small"],
                         anchor="w", justify="left", wraplength=self.app.px(660))
        label.pack(fill="x")
        return label

    def banner(self, text: str, status: str) -> None:
        tk.Label(self.body, text=text, bg=self.C[f"{status}_bg"], fg=self.C[f"{status}_fg"], font=self.app.F["bold"],
                 anchor="w", justify="left", padx=10, pady=6, wraplength=self.app.px(640)).pack(fill="x", pady=(6, 4))

    def table(self, columns: list[tuple[str, int, str]], rows: list[tuple[tuple, str]], height: int) -> ttk.Treeview:
        tree = ttk.Treeview(self.body, columns=[c[0] for c in columns], show="headings", height=height,
                            style="Meet.Treeview", selectmode="none")
        for name, width, anchor in columns:
            tree.heading(name, text=name)
            tree.column(name, width=self.app.px(width), anchor=anchor, stretch=name == columns[0][0])
        for style in ("ok", "weak", "bad"):
            tree.tag_configure(style, background=self.C[f"{style}_bg"], foreground=self.C[f"{style}_fg"])
        tree.tag_configure("muted", foreground=self.C["muted"])
        tree.tag_configure("dim", background=self.C["side"], font=self.app.F["small_bold"])
        for values, tag in rows:
            tree.insert("", "end", values=values, tags=(tag,))
        tree.pack(fill="x", pady=(6, 0))
        return tree

    # ------------------------------------------------------------------ the three sections

    def refresh(self) -> None:
        for child in self.body.winfo_children():
            child.destroy()
        rows = usagelog.read_rows(self.app.settings.usage_log)
        self._spending()
        self._usage(rows)
        self._scorecard()

    def _spending(self) -> None:
        s = self.app.settings
        status = guardrails.spend_status(s)
        self.heading("Spending, both engines together",
                     "Counted from your usage log, so the day and month totals still apply after a restart.")
        for label, spent, limit in (("This session", self.app.tracker.total, s.max_session_cost_usd),
                                    ("Today", status.today, status.daily_limit),
                                    ("This month", status.month, status.monthly_limit)):
            row = tk.Frame(self.body, bg=self.C["bg"])
            row.pack(fill="x", pady=(6, 0))
            tk.Label(row, text=label, width=12, anchor="w", bg=self.C["bg"], fg=self.C["text"],
                     font=self.app.F["small"]).pack(side="left")
            share = min(spent / limit, 1.0) if limit > 0 else 0.0
            ttk.Progressbar(row, maximum=100, value=share * 100, length=self.app.px(240)).pack(side="left", padx=(0, 10))
            text = f"{money_text(spent)} of {money_text(limit)}" if limit > 0 else f"{money_text(spent)} (no limit)"
            colour = self.C["bad_fg"] if limit > 0 and spent >= limit else self.C["text"]
            tk.Label(row, text=text, anchor="w", bg=self.C["bg"], fg=colour, font=self.app.F["small"]).pack(side="left")

    def _usage(self, rows: list[dict]) -> None:
        summary = reports.summarize_usage(rows, days=30, engine=self.engine)
        self.heading(f"Your questions answered with {self.engine_name}, last 30 days")
        if not summary.attempts:
            self.note(f"Nothing yet. Choose {self.engine_name} in the sidebar and ask a question; its speed and cost "
                      "show up here.")
            return
        if summary.no_match:
            self.note(f"{summary.no_match} question{'s' if summary.no_match != 1 else ''} matched no passage and "
                      "{} answered \"not found\" without calling Claude (no cost).".format(
                          "were" if summary.no_match != 1 else "was"))
        failure = summary.failure_rate
        parts = [f"{summary.answers} answer{'s' if summary.answers != 1 else ''}",
                 f"median {seconds_text(summary.p50)}", f"slowest 5% {seconds_text(summary.p95)}",
                 f"average {money_text(summary.average_cost)} per answer", f"{money_text(summary.total_cost)} in total"]
        self.note("  ·  ".join(parts), fg=self.C["text"])
        if summary.failed:
            self.note(f"{summary.failed} attempt{'s' if summary.failed != 1 else ''} gave no answer"
                      + (f"; {failure:.1%} of attempts failed for reasons outside your control (timeouts, outages)."
                         if failure is not None else "."))
        else:
            self.note("No failed attempts.")
        if summary.by_model:
            self.table([("Model", 190, "w"), ("Answers", 70, "e"), ("Median", 80, "e"), ("Slowest 5%", 90, "e"),
                        ("Avg cost", 90, "e"), ("Total", 90, "e")],
                       [((m.model, m.answers, seconds_text(m.p50), seconds_text(m.p95), money_text(m.average_cost),
                          money_text(m.total_cost)), "") for m in summary.by_model], height=min(len(summary.by_model), 4))

    def _scorecard(self) -> None:
        self.heading(f"Release scorecard: {self.engine_name}",
                     f"How this version of MeetMiner did on its own checks with the {self.engine_name} engine "
                     "(accuracy, speed, failures, cost, safety). It comes from the evals in the repository.")
        found = reports.find_scorecard(self.opened, self.engine)
        if not found:
            name = "latest-rag.json" if self.engine == "rag" else "latest.json"
            self.note("No scorecard is available on this PC. Use \"Open report file\" to load one "
                      f"(scorecards/{name} in the repository).")
            self.card_text = ""
        else:
            path, card = found
            self.banner(reports.headline(card), DECISION_STYLE.get(str(card.get("decision")), "weak"))
            for line in (card.get("gates_failed") or []):
                self.note(f"Blocked by: {line}", fg=self.C["bad_fg"])
            for line in (card.get("unmeasured") or []):
                self.note(f"Not measured yet: {line}", fg=self.C["weak_fg"])
            table_rows: list[tuple[tuple, str]] = []
            lines = [reports.headline(card)]
            for dim in card["dimensions"]:
                score = dim.get("score")
                label = f"{dim.get('name', '')}  ({score:.0f}/100, weight {dim.get('weight', 0):g}%)" \
                    if isinstance(score, (int, float)) else str(dim.get("name", ""))
                table_rows.append(((label, "", "", ""), "dim"))
                lines.append(label)
                for m in dim.get("metrics", []):
                    rating = str(m.get("rating", "info"))
                    table_rows.append(((("  " + str(m.get("name", ""))) + (" [gate]" if m.get("gate") else ""),
                                        m.get("target", ""), m.get("measured", ""), RATING_LABEL.get(rating, rating)),
                                       RATING_STYLE.get(rating, "muted")))
                    lines.append(f"  {m.get('name', '')}: {m.get('measured', '')} (target {m.get('target', '')}) "
                                 f"{RATING_LABEL.get(rating, rating)}")
            self.table([("Check", 330, "w"), ("Target", 150, "w"), ("Measured", 230, "w"), ("Result", 70, "w")],
                       table_rows, height=len(table_rows))  # the whole page scrolls, not the table
            self.note(f"Source: {reports.source_label(path)}")
            for line in card.get("notes") or []:
                self.note(str(line))
            self.card_text = "\n".join(lines)
        buttons = tk.Frame(self.body, bg=self.C["bg"])
        buttons.pack(fill="x", pady=(10, 14))
        self.app.button(buttons, "Open report file…", self.open_file).pack(side="left")
        self.app.button(buttons, "Refresh", self.refresh).pack(side="left", padx=6)
        self.app.button(buttons, "Copy summary", self.copy_summary).pack(side="left")

    # ------------------------------------------------------------------ actions

    def open_file(self) -> None:
        chosen = filedialog.askopenfilename(
            parent=self, title="Open a MeetMiner scorecard", filetypes=[("Scorecard (JSON)", "*.json"), ("All files", "*.*")],
            initialdir=str(storage.data_dir()))
        if not chosen:
            return
        try:
            reports.load_scorecard(Path(chosen))
        except reports.ScorecardError as exc:
            messagebox.showinfo(self.app.root.title(), str(exc), parent=self)
            return
        self.opened = Path(chosen)
        self.refresh()

    def copy_summary(self) -> None:
        if self.card_text:
            self.app.copy(self.card_text + f"\n\n(copied {datetime.now():%Y-%m-%d %H:%M})")
