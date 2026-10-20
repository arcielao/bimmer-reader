"""Fault memory tab: read, clear and export faults, plus the full report."""

from __future__ import annotations

import csv
import datetime as dt
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from bimmer.config import APP_NAME
from bimmer.diag import fmt, is_missing_file_error, job_status, parse_faults
from bimmer.gui.theme import COLORS


class FaultsTab:
    """App mixin: Fault memory tab and the full report."""

    def _build_faults_tab(self):
        tab = ttk.Frame(self.tabs, style="Panel.TFrame", padding=8)
        self.tabs.add(tab, text="Fault memory")
        bar = ttk.Frame(tab, style="Panel.TFrame")
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Read faults", style="Accent.TButton", command=self.read_faults).pack(side="left")
        ttk.Button(bar, text="Clear faults...", command=self.clear_faults).pack(side="left", padx=6)
        ttk.Button(bar, text="Export...", command=self.export_faults).pack(side="left")
        self.fault_summary = ttk.Label(bar, text="", style="Muted.TLabel")
        self.fault_summary.pack(side="left", padx=12)
        frame, self.fault_tree = self._tree(tab, ("Code", "Description", "Status", "Count", "P-code"), (60, 300, 170, 50, 60), height=10)
        frame.pack(fill="both", expand=True)
        self.fault_tree.tag_configure("present", foreground=COLORS["bad"])
        self.fault_tree.bind("<<TreeviewSelect>>", lambda e: self._show_fault_detail())
        ttk.Label(tab, text="All fields for the selected fault (environment data, mileage, etc.)", style="Muted.TLabel").pack(anchor="w", pady=(8, 2))
        self.fault_detail = tk.Text(tab, height=8, font=self.mono, relief="solid", bd=1, wrap="none")
        self.fault_detail.pack(fill="x")

    def _fill_faults(self, faults, read=True):
        self.current_faults = faults
        self.fault_tree.delete(*self.fault_tree.get_children())
        self.fault_detail.delete("1.0", "end")
        for i, f in enumerate(faults):
            present = "present" in f["status"].lower() and "not present" not in f["status"].lower() or "momentan vorhanden" in f["status"].lower()
            self.fault_tree.insert("", "end", iid=str(i), values=(f["code"], f["text"], f["status"], f["count"], f["pcode"]),
                                   tags=("present",) if present else ())
        if not read:
            self.fault_summary.configure(text="Not read yet")
        else:
            self.fault_summary.configure(text="No faults stored" if not faults else f"{len(faults)} fault(s)")

    def _show_fault_detail(self):
        sel = self.fault_tree.selection()
        self.fault_detail.delete("1.0", "end")
        if not sel:
            return
        raw = self.current_faults[int(sel[0])]["raw"]
        width = max((len(k) for k in raw), default=10)
        def show(k, v):
            return f"0x{v:04X} ({v})" if k == "F_ORT_NR" and isinstance(v, int) else fmt(v)
        self.fault_detail.insert("end", "\n".join(f"{k:<{width}}  {show(k, v)}" for k, v in raw.items()))

    def read_faults(self):
        if not self._need_connection():
            return
        mod = self._selected_module()
        if not mod:
            return
        self.log(f"Reading fault memory: {mod['name']}...")

        def work():
            sgbd = self._resolve(mod)
            _, sets = self.backend.job(sgbd, "FS_LESEN")
            return sets

        def done(sets, err):
            if err:
                mod["state"] = "Not installed" if is_missing_file_error(str(err)) else "No response"
                self._refresh_module_tree()
                self.log(f"{mod['name']}: {err}", "err")
                return
            mod["faults"] = parse_faults(sets)
            mod["state"] = "OK"
            st = job_status(sets)
            self._refresh_module_tree()
            self._on_module_selected()
            self.log(f"{mod['name']}: {len(mod['faults'])} fault(s). JOB_STATUS={st or '?'}", "ok" if st in ("OKAY", "") else "err")

        self.run(work, cb=done)

    def clear_faults(self):
        if not self._need_connection():
            return
        mod = self._selected_module()
        if not mod:
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"Clear the fault memory in {mod['name']}?\n\n"
            "Codes can't be recovered after this. Export them first if you want a record. "
            "Clearing doesn't fix anything - a real fault will come back.",
            icon="warning",
        ):
            return
        self.log(f"Clearing fault memory: {mod['name']}...")

        def work():
            sgbd = self._resolve(mod)
            _, sets = self.backend.job(sgbd, "FS_LOESCHEN")
            st = job_status(sets)
            _, after = self.backend.job(sgbd, "FS_LESEN")
            return st, after

        def done(res, err):
            if err:
                self.log(f"{mod['name']}: clear failed: {err}", "err")
                return
            st, after = res
            mod["faults"] = parse_faults(after)
            self._refresh_module_tree()
            self._on_module_selected()
            left = len(mod["faults"])
            self.log(f"{mod['name']}: cleared (JOB_STATUS={st or '?'}). {left} fault(s) came straight back." if left
                     else f"{mod['name']}: cleared (JOB_STATUS={st or '?'}). Fault memory is empty.", "ok")

        self.run(work, cb=done)

    def export_faults(self):
        mod = self._selected_module()
        if not mod or mod["faults"] is None:
            messagebox.showinfo(APP_NAME, "Read the faults first.")
            return
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=f"faults_{mod.get('resolved') or 'module'}_{stamp}.csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Module", "Variant", "Code", "Description", "Status", "Count", "P-code", "All fields"])
            for flt in mod["faults"]:
                w.writerow([mod["name"], mod["variant"], flt["code"], flt["text"], flt["status"], flt["count"], flt["pcode"],
                            "; ".join(f"{k}={fmt(v)}" for k, v in flt["raw"].items())])
        self.log(f"Saved {path}", "ok")

    def save_report(self):
        read = [m for m in self.modules if m["state"]]
        if not read:
            messagebox.showinfo(APP_NAME, "Run a Quick test first.")
            return
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
        path = filedialog.asksaveasfilename(defaultextension=".txt", initialfile=f"bimmer_report_{stamp}.txt",
                                            filetypes=[("Text", "*.txt")])
        if not path:
            return
        lines = [f"{APP_NAME} report - {self.config.get('vehicle', '')}", dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
                 f"Battery: {self.bat_lbl.cget('text').split(': ')[-1]}   Ignition: {self.ign_lbl.cget('text').split(': ')[-1]}", ""]
        for m in self.modules:
            lines.append(f"== {m['name']}  [{m.get('resolved') or m['sgbd'][0]} {m['variant']}]  {m['state'] or 'not tested'}")
            for flt in m["faults"] or []:
                pc = f"  ({flt['pcode']})" if flt["pcode"] else ""
                lines.append(f"   {flt['code']}{pc}  {flt['text']}")
                if flt["status"] or flt["count"]:
                    lines.append(f"          {flt['status']}  count={flt['count']}")
            if m["state"] == "OK" and not m["faults"]:
                lines.append("   no faults")
            lines.append("")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        self.log(f"Saved {path}", "ok")
