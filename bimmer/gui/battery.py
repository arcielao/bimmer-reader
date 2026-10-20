"""Battery registration in the DME."""

from __future__ import annotations

from tkinter import messagebox

from bimmer.config import APP_NAME
from bimmer.diag import fmt, job_status


class BatteryActions:
    """App mixin: Battery registration."""

    BATTERY_JOBS = ("STEUERN_BATTERIETAUSCH_REGISTRIEREN", "BATTERIETAUSCH_REGISTRIEREN", "STEUERN_BATTERIE_TAUSCH_REGISTRIEREN")

    def _dme_module(self) -> dict | None:
        for m in self.modules:
            if any(s.upper() in ("D_0012", "D_MOTOR") for s in m["sgbd"]) or m["name"].upper().startswith("DME"):
                return m
        return None

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
            self.run(lambda: job_status(self.backend.job(self._resolve(mod), job)[1]), cb=done)

        def done(st, err):
            if err:
                self.log(f"{mod['name']}: battery registration failed: {err}", "err")
                messagebox.showerror(APP_NAME, f"Battery registration failed:\n\n{err}")
                return
            if st in ("OKAY", ""):
                self.log(f"{mod['name']}: new battery registered (JOB_STATUS={st or '-'}).", "ok")
                messagebox.showinfo(APP_NAME, "New battery registered.")
            else:
                self.log(f"{mod['name']}: battery registration answered JOB_STATUS={st}.", "err")
                messagebox.showwarning(APP_NAME, f"The DME answered JOB_STATUS={st}. The battery may not be registered.")

        self.run(find, cb=found)
