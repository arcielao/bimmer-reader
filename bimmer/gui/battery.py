"""Battery window: registration in the DME and when it was last done."""

from __future__ import annotations

import datetime as dt
import tkinter as tk
from tkinter import messagebox, ttk

from bimmer.config import APP_NAME, BATTERY_HISTORY_FILE, load_json, save_json
from bimmer.diag import DiagError, fmt, job_status, pair_units
from bimmer.gui.theme import COLORS

WRITE_WORDS = ("SCHREIBEN", "LOESCHEN", "STEUERN", "REGIST", "RESET", "CODIER", "FLASH", "PROGRAMM")


def is_battery_history_job(job: str) -> bool:
    """Read-only DME jobs that report battery replacement history."""
    j = job.upper()
    read_only = (j.startswith("STATUS_") or j.endswith("_LESEN")) and not any(w in j for w in WRITE_WORDS)
    return read_only and ("BATTERIETAUSCH" in j or ("BATT" in j and "HIST" in j))


class BatteryActions:
    """App mixin: Battery window."""

    BATTERY_JOBS = ("STEUERN_BATTERIETAUSCH_REGISTRIEREN", "BATTERIETAUSCH_REGISTRIEREN", "STEUERN_BATTERIE_TAUSCH_REGISTRIEREN")
    battery_win: tk.Toplevel | None = None

    def _dme_module(self) -> dict | None:
        for m in self.modules:
            if any(s.upper() in ("D_0012", "D_MOTOR") for s in m["sgbd"]) or m["name"].upper().startswith("DME"):
                return m
        return None

    def open_battery(self):
        if self.battery_win is not None and self.battery_win.winfo_exists():
            self.battery_win.lift()
            return
        win = self.battery_win = tk.Toplevel(self.root)
        win.title(f"{APP_NAME} - Battery")
        win.geometry("660x540")
        win.configure(bg=COLORS["panel"])
        win.protocol("WM_DELETE_WINDOW", self._close_battery)
        f = ttk.Frame(win, style="Panel.TFrame", padding=12)
        f.pack(fill="both", expand=True)
        self.bat_last_lbl = ttk.Label(f, text="", style="Title.TLabel")
        self.bat_last_lbl.pack(anchor="w")

        ttk.Label(f, text="Recorded by the car (DME)", style="Panel.TLabel", font=(self.font[0], 10, "bold")).pack(anchor="w", pady=(12, 0))
        ttk.Label(f, text="Straight from the DME's battery history jobs, if it has any. Names are BMW's.",
                  style="Muted.TLabel").pack(anchor="w")
        frame, self.bat_car_tree = self._tree(f, ("Job", "Result", "Value", "Unit"), (200, 210, 100, 60), height=6)
        frame.pack(fill="both", expand=True, pady=(4, 0))
        ttk.Button(f, text="Read from car", command=self.read_battery_history).pack(anchor="w", pady=(4, 0))

        ttk.Label(f, text="Registered with this app", style="Panel.TLabel", font=(self.font[0], 10, "bold")).pack(anchor="w", pady=(12, 0))
        frame, self.bat_hist_tree = self._tree(f, ("When", "Module", "Job", "Status"), (140, 130, 270, 60), height=5)
        frame.pack(fill="both", expand=True, pady=(4, 0))

        row = ttk.Frame(f, style="Panel.TFrame")
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text="Register new battery...", style="Accent.TButton", command=self.register_battery).pack(side="left")
        ttk.Button(row, text="Close", command=self._close_battery).pack(side="right")

        self._fill_battery_history()
        if self.backend and self.backend.connected:
            self.read_battery_history()

    def _close_battery(self):
        if self.battery_win is not None:
            self.battery_win.destroy()
        self.battery_win = None

    def _battery_open(self) -> bool:
        return self.battery_win is not None and self.battery_win.winfo_exists()

    def _battery_history(self) -> list[dict]:
        h = load_json(BATTERY_HISTORY_FILE, [])
        return h if isinstance(h, list) else []

    def _record_battery(self, mod: dict, job: str, status: str):
        h = self._battery_history()
        h.append({"time": dt.datetime.now().isoformat(timespec="seconds"), "vehicle": self.config.get("vehicle", ""),
                  "module": mod["name"], "variant": mod["variant"], "job": job, "status": status or "-"})
        save_json(BATTERY_HISTORY_FILE, h)

    def _fill_battery_history(self):
        if not self._battery_open():
            return
        h = self._battery_history()
        self.bat_hist_tree.delete(*self.bat_hist_tree.get_children())
        for e in reversed(h):
            self.bat_hist_tree.insert("", "end", values=(e.get("time", "?").replace("T", " "), e.get("module", ""),
                                                         e.get("job", ""), e.get("status", "")))
        if h:
            last = h[-1]
            when = dt.datetime.fromisoformat(last["time"]) if last.get("time") else None
            days = (dt.datetime.now() - when).days if when else None
            ago = "" if days is None else " (today)" if days == 0 else " (1 day ago)" if days == 1 else f" ({days} days ago)"
            self.bat_last_lbl.configure(text=f"Last registered with this app: {last.get('time', '?').replace('T', ' ')[:16]}{ago}")
        else:
            self.bat_last_lbl.configure(text="Not registered with this app yet")

    def read_battery_history(self):
        if not self._need_connection():
            return
        mod = self._dme_module()
        if not mod:
            messagebox.showinfo(APP_NAME, "No DME (engine) module in the list. Add one with SGBD D_0012 first.")
            return

        def work():
            sgbd = self._resolve(mod)
            _, sets = self.backend.job(sgbd, "_JOBS")
            jobs = sorted({fmt(s.get("JOBNAME", "")) for s in sets if s.get("JOBNAME")})
            out = []
            for j in (j for j in jobs if is_battery_history_job(j)):
                try:
                    out.append((j, pair_units(self.backend.job(sgbd, j)[1]), None))
                except DiagError as e:
                    out.append((j, [], str(e)))
            return out

        def done(res, err):
            if not self._battery_open():
                return
            tree = self.bat_car_tree
            tree.delete(*tree.get_children())
            if err:
                tree.insert("", "end", values=("ERROR", str(err), "", ""))
                self.log(f"{mod['name']}: {err}", "err")
                return
            for job, rows, e in res:
                for name, value, unit in rows or [("ERROR", e or "no results", "")]:
                    tree.insert("", "end", values=(job, name, fmt(value), unit))
            if not res:
                tree.insert("", "end", values=("-", "This DME has no battery history job", "", ""))
            self.log(f"{mod['name']}: {len(res)} battery history job(s) read.")

        self.run(work, cb=done)

    def register_battery(self):
        if not self._need_connection():
            return
        mod = self._dme_module()
        if not mod:
            messagebox.showinfo(APP_NAME, "No DME (engine) module in the list. Add one with SGBD D_0012 first.")
            return
        self.log(f"Battery registration: looking for the job in {mod['name']}...")

        def find():
            sgbd = self._resolve(mod)
            _, sets = self.backend.job(sgbd, "_JOBS")
            jobs = {fmt(s.get("JOBNAME", "")).upper() for s in sets if s.get("JOBNAME")}
            match = next((j for j in self.BATTERY_JOBS if j in jobs), None)
            if not match:
                match = next((j for j in sorted(jobs) if "BATTERIETAUSCH" in j and "REGIST" in j), None)
            return match, sorted(j for j in jobs if "BATT" in j or "IBS" in j)

        def found(res, err):
            if err:
                self.log(f"{mod['name']}: {err}", "err")
                return
            job, related = res
            if not job:
                self.log(f"{mod['name']}: no battery registration job found. Battery-related jobs: {', '.join(related) or 'none'}", "err")
                messagebox.showinfo(APP_NAME, "This DME doesn't offer a battery registration job.\n\n"
                                    "The battery-related jobs it does have are listed in the log. "
                                    "You can run one from the Job runner if you know it's the right one.")
                return
            if not messagebox.askyesno(
                APP_NAME,
                "Register a new battery?\n\n"
                "Only do this after fitting a NEW battery of the SAME type and capacity as the old one. "
                "A different capacity or type (e.g. standard to AGM) needs coding instead.\n\n"
                f"Ignition must be ON, engine off.\n\nJob: {job}",
                icon="warning",
            ):
                return
            self.log(f"{mod['name']}: running {job}...")
            self.run(lambda: job_status(self.backend.job(self._resolve(mod), job)[1]), cb=lambda st, e: done(job, st, e))

        def done(job, st, err):
            if err:
                self.log(f"{mod['name']}: battery registration failed: {err}", "err")
                messagebox.showerror(APP_NAME, f"Battery registration failed:\n\n{err}")
                return
            if st in ("OKAY", ""):
                self._record_battery(mod, job, st)
                self.log(f"{mod['name']}: new battery registered (JOB_STATUS={st or '-'}).", "ok")
                self._fill_battery_history()
                if self._battery_open():
                    self.read_battery_history()
                messagebox.showinfo(APP_NAME, "New battery registered.")
            else:
                self.log(f"{mod['name']}: battery registration answered JOB_STATUS={st}.", "err")
                messagebox.showwarning(APP_NAME, f"The DME answered JOB_STATUS={st}. The battery may not be registered.")

        self.run(find, cb=found)
