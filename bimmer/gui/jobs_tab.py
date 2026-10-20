"""Job runner tab (Tool32 style) and the shared job-list fetch."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from bimmer.config import APP_NAME
from bimmer.diag import fmt, job_status


class JobsTab:
    """App mixin: Job runner tab."""

    def _build_jobs_tab(self):
        tab = ttk.Frame(self.tabs, style="Panel.TFrame", padding=8)
        self.tabs.add(tab, text="Job runner")
        pan = ttk.PanedWindow(tab, orient="horizontal")
        pan.pack(fill="both", expand=True)

        lf = ttk.Frame(pan, style="Panel.TFrame")
        pan.add(lf, weight=1)
        ttk.Button(lf, text="List jobs in module", command=self.load_jobs).pack(anchor="w")
        self.job_filter = tk.StringVar()
        ttk.Entry(lf, textvariable=self.job_filter).pack(fill="x", pady=(6, 2))
        self.job_filter.trace_add("write", lambda *a: self._fill_job_list())
        jbf = ttk.Frame(lf, style="Panel.TFrame")
        jbf.pack(fill="both", expand=True)
        self.job_list = tk.Listbox(jbf, font=self.mono, relief="solid", bd=1, exportselection=False)
        sb = ttk.Scrollbar(jbf, orient="vertical", command=self.job_list.yview)
        self.job_list.configure(yscrollcommand=sb.set)
        self.job_list.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.job_list.bind("<<ListboxSelect>>", lambda e: self._job_picked())
        self.job_list.bind("<Double-Button-1>", lambda e: self.run_job())

        rf = ttk.Frame(pan, style="Panel.TFrame")
        pan.add(rf, weight=2)
        row = ttk.Frame(rf, style="Panel.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="Job", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.job_name = tk.StringVar()
        ttk.Entry(row, textvariable=self.job_name, width=34).grid(row=0, column=1, sticky="we", padx=6)
        ttk.Label(row, text="Arguments", style="Panel.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.job_args = tk.StringVar()
        ttk.Entry(row, textvariable=self.job_args, width=34).grid(row=1, column=1, sticky="we", padx=6, pady=(4, 0))
        ttk.Button(row, text="Run", style="Accent.TButton", command=self.run_job).grid(row=0, column=2, rowspan=2, padx=6, sticky="ns")
        self.show_sys = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Show system set", variable=self.show_sys).grid(row=0, column=3, rowspan=2, padx=6)
        row.columnconfigure(1, weight=1)
        ttk.Label(rf, text="Separate multiple arguments with ;   Jobs that write/code/flash are your responsibility - read before you run.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 6))
        frame, self.job_tree = self._tree(rf, ("Set", "Result", "Value"), (50, 240, 330), height=14)
        frame.pack(fill="both", expand=True)

    def _fetch_jobs(self, mod, cb):
        def work():
            sgbd = self._resolve(mod)
            _, sets = self.backend.job(sgbd, "_JOBS")
            return sorted({fmt(s.get("JOBNAME", "")) for s in sets if s.get("JOBNAME")})

        def done(jobs, err):
            if err:
                self.log(f"{mod['name']}: couldn't list jobs: {err}", "err")
                return
            self.current_jobs = jobs
            self.log(f"{mod['name']}: {len(jobs)} jobs available.")
            cb()

        self.run(work, cb=done)

    def load_jobs(self):
        if self._need_connection() and (mod := self._selected_module()):
            self._fetch_jobs(mod, self._fill_job_list)

    def _fill_job_list(self):
        f = self.job_filter.get().upper()
        self.job_list.delete(0, "end")
        for j in self.current_jobs:
            if f in j.upper():
                self.job_list.insert("end", j)

    def _job_picked(self):
        sel = self.job_list.curselection()
        if sel:
            self.job_name.set(self.job_list.get(sel[0]))

    def run_job(self):
        if not self._need_connection():
            return
        mod = self._selected_module()
        job = self.job_name.get().strip()
        if not mod or not job:
            return
        args = self.job_args.get().strip()
        if any(w in job.upper() for w in ("SCHREIBEN", "LOESCHEN", "CODIER", "FLASH", "PROGRAMM", "STEUERN", "RESET")):
            if not messagebox.askyesno(APP_NAME, f"'{job}' looks like it changes something in the module "
                                       "(writes, clears, codes, actuates or resets).\n\nRun it anyway?", icon="warning"):
                return

        def work():
            sgbd = self._resolve(mod)
            return self.backend.job(sgbd, job, args)

        def done(res, err):
            self.job_tree.delete(*self.job_tree.get_children())
            if err:
                self.log(f"{mod['name']} {job}: {err}", "err")
                return
            sys_set, sets = res
            if self.show_sys.get():
                for k, v in sys_set.items():
                    self.job_tree.insert("", "end", values=("sys", k, fmt(v)))
            for i, s in enumerate(sets, 1):
                for k, v in s.items():
                    self.job_tree.insert("", "end", values=(i, k, fmt(v)))
            st = job_status(sets)
            self.log(f"{mod['name']} {job}: {len(sets)} result set(s). JOB_STATUS={st or '-'}", "ok" if st in ("OKAY", "") else "err")

        self.run(work, cb=done)
