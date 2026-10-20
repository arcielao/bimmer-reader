"""Live data tab: polls STATUS_* jobs, optional CSV logging."""

from __future__ import annotations

import csv
import datetime as dt
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from bimmer.config import APP_NAME
from bimmer.diag import DiagError, fmt, pair_units


class LiveTab:
    """App mixin: Live data tab."""

    def _build_live_tab(self):
        tab = ttk.Frame(self.tabs, style="Panel.TFrame", padding=8)
        self.tabs.add(tab, text="Live data")
        pan = ttk.PanedWindow(tab, orient="horizontal")
        pan.pack(fill="both", expand=True)

        lf = ttk.Frame(pan, style="Panel.TFrame")
        pan.add(lf, weight=1)
        top = ttk.Frame(lf, style="Panel.TFrame")
        top.pack(fill="x")
        ttk.Button(top, text="Load STATUS jobs", command=self.load_live_jobs).pack(side="left")
        ttk.Label(lf, text="Select one or more:", style="Muted.TLabel").pack(anchor="w", pady=(6, 2))
        self.live_filter = tk.StringVar()
        ent = ttk.Entry(lf, textvariable=self.live_filter)
        ent.pack(fill="x")
        self.live_filter.trace_add("write", lambda *a: self._fill_live_list())
        lbf = ttk.Frame(lf, style="Panel.TFrame")
        lbf.pack(fill="both", expand=True, pady=(4, 0))
        self.live_list = tk.Listbox(lbf, selectmode="extended", font=self.mono, relief="solid", bd=1, exportselection=False)
        sb = ttk.Scrollbar(lbf, orient="vertical", command=self.live_list.yview)
        self.live_list.configure(yscrollcommand=sb.set)
        self.live_list.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        rf = ttk.Frame(pan, style="Panel.TFrame")
        pan.add(rf, weight=2)
        ctl = ttk.Frame(rf, style="Panel.TFrame")
        ctl.pack(fill="x", pady=(0, 6))
        self.live_btn = ttk.Button(ctl, text="Start", style="Accent.TButton", command=self.toggle_live)
        self.live_btn.pack(side="left")
        ttk.Label(ctl, text="Every", style="Panel.TLabel").pack(side="left", padx=(12, 4))
        self.live_interval = tk.IntVar(value=500)
        ttk.Spinbox(ctl, from_=100, to=10000, increment=100, width=6, textvariable=self.live_interval).pack(side="left")
        ttk.Label(ctl, text="ms", style="Panel.TLabel").pack(side="left", padx=4)
        self.live_log_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ctl, text="Log to CSV", variable=self.live_log_var).pack(side="left", padx=12)
        self.live_rate = ttk.Label(ctl, text="", style="Muted.TLabel")
        self.live_rate.pack(side="left", padx=8)
        frame, self.live_tree = self._tree(rf, ("Job", "Result", "Value", "Unit"), (170, 175, 80, 60), height=14)
        frame.pack(fill="both", expand=True)

    def load_live_jobs(self):
        if self._need_connection() and (mod := self._selected_module()):
            self._fetch_jobs(mod, self._fill_live_list)

    def _fill_live_list(self):
        f = self.live_filter.get().upper()
        self.live_list.delete(0, "end")
        for j in self.current_jobs:
            if j.upper().startswith("STATUS_") and f in j.upper():
                self.live_list.insert("end", j)

    def toggle_live(self):
        if self.live_running:
            self.stop_live()
        else:
            self.start_live()

    def start_live(self):
        if not self._need_connection():
            return
        mod = self._selected_module()
        if not mod:
            return
        jobs = [self.live_list.get(i) for i in self.live_list.curselection()]
        if not jobs:
            messagebox.showinfo(APP_NAME, "Load the STATUS jobs and select at least one.")
            return
        if self.live_log_var.get():
            stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=f"live_{stamp}.csv", filetypes=[("CSV", "*.csv")])
            if not path:
                return
            self.live_csv = open(path, "w", newline="", encoding="utf-8")
            self.live_writer = csv.writer(self.live_csv)
            self.live_writer.writerow(["timestamp", "module", "job", "result", "value", "unit"])
            self.log(f"Logging live data to {path}")
        self.live_tree.delete(*self.live_tree.get_children())
        self.live_rows = {}
        self.live_running = True
        self.live_mod, self.live_jobs = mod, jobs
        self._live_count, self._live_t0 = 0, time.time()
        self.live_btn.configure(text="Stop")
        self.log(f"Live data started: {', '.join(jobs)}")
        self._live_tick()

    def stop_live(self):
        if not self.live_running:
            return
        self.live_running = False
        self.live_btn.configure(text="Start")
        if self.live_csv:
            self.live_csv.close()
            self.live_csv = self.live_writer = None
        self.log("Live data stopped.")

    def _live_tick(self):
        if not self.live_running:
            return
        mod, jobs = self.live_mod, list(self.live_jobs)

        def work():
            sgbd = self._resolve(mod)
            out = []
            for j in jobs:
                try:
                    _, sets = self.backend.job(sgbd, j)
                    out.append((j, pair_units(sets), None))
                except DiagError as e:
                    out.append((j, [], str(e)))
            return out

        def done(res, err):
            if not self.live_running:
                return
            if err:
                self.log(f"Live data error: {err}", "err")
                self.stop_live()
                return
            now = dt.datetime.now().isoformat(timespec="milliseconds")
            for job, rows, e in res:
                if e:
                    rows = [("ERROR", e, "")]
                for name, value, unit in rows:
                    key = (job, name)
                    vals = (job, name, fmt(value), unit)
                    if key in self.live_rows:
                        self.live_tree.item(self.live_rows[key], values=vals)
                    else:
                        self.live_rows[key] = self.live_tree.insert("", "end", values=vals)
                    if self.live_writer:
                        self.live_writer.writerow([now, mod.get("resolved"), job, name, fmt(value), unit])
            self._live_count += 1
            el = max(time.time() - self._live_t0, 1e-6)
            self.live_rate.configure(text=f"{self._live_count / el:.1f} updates/s")
            delay = max(int(self.live_interval.get() or 500), 50)
            self.root.after(delay, self._live_tick)

        self.run(work, cb=done)
