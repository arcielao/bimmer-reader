"""Coding tab: read/back up coding data, recipes and restore."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from bimmer.backends.demo import DemoBackend
from bimmer.coding import apply_recipe, build_args, coding_bytes, hex_dump, recipe_state, to_int
from bimmer.config import APP_NAME, BACKUP_DIR, HERE, load_json
from bimmer.diag import DiagError, fmt, job_status


class CodingTab:
    """App mixin: Coding tab."""

    def _build_coding_tab(self):
        tab = ttk.Frame(self.tabs, style="Panel.TFrame", padding=8)
        self.tabs.add(tab, text="Coding")
        bar = ttk.Frame(tab, style="Panel.TFrame")
        bar.pack(fill="x", pady=(0, 6))
        ttk.Label(bar, text="Block", style="Panel.TLabel").pack(side="left")
        self.coding_profile = tk.StringVar()
        self.coding_combo = ttk.Combobox(bar, textvariable=self.coding_profile, state="readonly", width=26)
        self.coding_combo.pack(side="left", padx=6)
        self.coding_combo.bind("<<ComboboxSelected>>", lambda e: self._coding_reset())
        ttk.Button(bar, text="Read coding", style="Accent.TButton", command=self.read_coding).pack(side="left")
        ttk.Button(bar, text="Restore backup...", command=self.restore_coding).pack(side="left", padx=6)
        ttk.Button(bar, text="Show coding jobs", command=self.show_coding_jobs).pack(side="left")
        self.coding_status = ttk.Label(tab, text="", style="Muted.TLabel")
        self.coding_status.pack(anchor="w")
        frame, self.recipe_tree = self._tree(tab, ("Setting", "Byte", "Now", "Notes"), (230, 50, 70, 300), height=6)
        frame.pack(fill="both", expand=True, pady=(4, 0))
        row = ttk.Frame(tab, style="Panel.TFrame")
        row.pack(fill="x", pady=6)
        ttk.Button(row, text="Turn on", command=lambda: self.set_recipe(True)).pack(side="left")
        ttk.Button(row, text="Turn off", command=lambda: self.set_recipe(False)).pack(side="left", padx=6)
        ttk.Label(row, text="Every write saves a backup first and reads the module back to check it.",
                  style="Muted.TLabel").pack(side="left", padx=8)
        self.coding_text = tk.Text(tab, height=9, font=self.mono, relief="solid", bd=1, wrap="none")
        self.coding_text.pack(fill="x")

    def _profiles_for(self, mod: dict) -> list[dict]:
        names = {n.upper() for n in [mod["variant"], mod.get("resolved") or "", *mod["sgbd"]] if n}
        demo = isinstance(self.backend, DemoBackend) or (self.backend is None and self.demo_var.get())
        return [p for p in self.coding_cfg.get("profiles", [])
                if p.get("variant", "").upper() in names and (demo or not p.get("demo_only"))]

    def _current_profile(self, mod: dict) -> dict | None:
        return next((p for p in self._profiles_for(mod) if p.get("name") == self.coding_profile.get()), None)

    def _refresh_coding_profiles(self, mod: dict):
        names = [p.get("name", "?") for p in self._profiles_for(mod)]
        self.coding_combo.configure(values=names)
        if self.coding_profile.get() not in names:
            self.coding_profile.set(names[0] if names else "")
        if not (self.coding and self.coding["mod"] is mod and self.coding["profile"].get("name") == self.coding_profile.get()):
            self._coding_reset()
        else:
            self._show_coding()

    def _coding_reset(self):
        self.coding = None
        mod = self._selected_module(quiet=True)
        if mod and not self.coding_combo.cget("values"):
            hint = "No coding profile for this module yet. Use Show coding jobs, then add one to coding_e90.json."
            self.coding_status.configure(text=hint if mod["variant"] else "Read coding (or run a Quick test) to find this module's variant.")
        else:
            self.coding_status.configure(text="Not read yet")
        self._show_coding()

    def _show_coding(self):
        mod = self._selected_module(quiet=True)
        profile = self.coding["profile"] if self.coding else (self._current_profile(mod) if mod else None)
        data = self.coding["data"] if self.coding else None
        sel = self.recipe_tree.selection()
        self.recipe_tree.delete(*self.recipe_tree.get_children())
        for i, r in enumerate((profile or {}).get("recipes", [])):
            self.recipe_tree.insert("", "end", iid=str(i), values=(r.get("name", "?"), fmt(r.get("byte", "")),
                                                                    recipe_state(data, r) if data is not None else "?", r.get("description", "")))
        keep = [i for i in sel if self.recipe_tree.exists(i)]
        if keep:
            self.recipe_tree.selection_set(keep)
        self.coding_text.delete("1.0", "end")
        if data is not None:
            self.coding_text.insert("end", hex_dump(data))

    def _read_coding_data(self, mod: dict, p: dict) -> bytes:
        """Runs in the worker."""
        r = p["read"]
        _, sets = self.backend.job(self._resolve(mod), r["job"], build_args(r.get("args", "")), raw=True)
        st = job_status(sets)
        if st not in ("OKAY", ""):
            raise DiagError(f"{r['job']} answered JOB_STATUS={st}")
        return coding_bytes(sets, r["result"])

    def _save_backup(self, mod: dict, p: dict, data: bytes, reason: str) -> str:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        tag = re.sub(r"[^A-Za-z0-9]+", "_", f"{mod['variant'] or mod.get('resolved')}_{p.get('name', '')}").strip("_")
        path = os.path.join(BACKUP_DIR, f"{tag}_{stamp}_{reason}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"module": mod["name"], "sgbd": mod.get("resolved"), "variant": mod["variant"], "profile": p.get("name"),
                       "time": dt.datetime.now().isoformat(timespec="seconds"), "reason": reason,
                       "length": len(data), "hex": data.hex(" ").upper()}, f, indent=2)
        return path

    def read_coding(self):
        if not self._need_connection():
            return
        mod = self._selected_module()
        if not mod:
            return

        def resolved(_, err):
            if err:
                self.log(f"{mod['name']}: {err}", "err")
                return
            self._refresh_module_tree()
            self._refresh_coding_profiles(mod)
            p = self._current_profile(mod)
            if not p:
                messagebox.showinfo(APP_NAME, f"There's no coding profile for {mod['variant'] or mod.get('resolved')} yet.\n\n"
                                    "Click Show coding jobs to see what this module offers, then add a profile to coding_e90.json.")
                return
            self.log(f"{mod['name']}: reading coding ({p.get('name')})...")
            self.run(lambda: self._read_coding_data(mod, p), cb=lambda data, e: done(p, data, e))

        def done(p, data, err):
            if err:
                self.log(f"{mod['name']}: coding read failed: {err}", "err")
                return
            path = self._save_backup(mod, p, data, "read")
            self.coding = {"mod": mod, "profile": p, "data": data}
            self.coding_status.configure(text=f"{len(data)} bytes read at {dt.datetime.now():%H:%M:%S}. Backup: {os.path.basename(path)}")
            self._show_coding()
            self.log(f"{mod['name']}: {len(data)} bytes of coding read. Backup saved to {path}", "ok")

        self.run(lambda: self._resolve(mod), cb=resolved)

    def _write_coding(self, mod: dict, p: dict, old: bytes, new: bytes, reason: str):
        """Runs in the worker: battery check, re-read, backup, write, read back."""
        try:
            volts = self._first_number(self.backend.job("UTILITY", "STATUS_UBATT")[1])
        except DiagError:
            volts = None  # can't measure; don't block on it
        if volts is not None and (volts / 1000 if volts > 100 else volts) < 12.0:
            raise DiagError("Battery is under 12 V. Put a charger on before coding.")
        current = self._read_coding_data(mod, p)
        if current != old:
            raise DiagError("The module's coding has changed since it was read. Read it again first.")
        if len(new) != len(current):
            raise DiagError(f"New data is {len(new)} bytes but the module has {len(current)}.")
        backup = self._save_backup(mod, p, current, f"before_{reason}")
        try:
            w = p["write"]
            _, sets = self.backend.job(self._resolve(mod), w["job"], build_args(w.get("args", ""), new), raw=True)
            st = job_status(sets)
            if st not in ("OKAY", ""):
                raise DiagError(f"{w['job']} answered JOB_STATUS={st}")
            after = self._read_coding_data(mod, p)
        except DiagError as e:
            raise DiagError(f"{e}\nBackup of the previous coding: {backup}")
        return backup, after

    def _start_write(self, mod: dict, p: dict, new: bytes, reason: str, what: str):
        old = self.coding["data"]
        self.stop_live()
        self.log(f"{mod['name']}: writing coding ({what})...")

        def done(res, err):
            if err:
                self.log(f"{mod['name']}: coding write failed: {err}", "err")
                messagebox.showerror(APP_NAME, f"Coding write failed:\n\n{err}")
                return
            backup, after = res
            self.coding["data"] = after
            self.coding_status.configure(text=f"{len(after)} bytes, written at {dt.datetime.now():%H:%M:%S}. "
                                              f"Backup: {os.path.basename(backup)}")
            self._show_coding()
            if after == new:
                self.log(f"{mod['name']}: {what} written and verified. Backup: {backup}", "ok")
                messagebox.showinfo(APP_NAME, f"Done: {what}.\n\nSome changes only take effect after the ignition "
                                    "has been off and the car has gone to sleep (lock it and wait a few minutes).")
            else:
                self.log(f"{mod['name']}: read-back doesn't match what was written! Backup: {backup}", "err")
                messagebox.showerror(APP_NAME, "The module accepted the write, but reading it back gives different data.\n\n"
                                     f"Restore the backup if anything misbehaves:\n{backup}")

        self.run(lambda: self._write_coding(mod, p, old, new, reason), cb=done)

    def _coding_ready(self) -> dict | None:
        if not self._need_connection():
            return None
        mod = self._selected_module()
        if not mod:
            return None
        if not (self.coding and self.coding["mod"] is mod):
            messagebox.showinfo(APP_NAME, "Read coding first.")
            return None
        return mod

    def set_recipe(self, on: bool):
        mod = self._coding_ready()
        if not mod:
            return
        sel = self.recipe_tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Pick a setting in the list first.")
            return
        p, data = self.coding["profile"], self.coding["data"]
        r = p["recipes"][int(sel[0])]
        try:
            new = apply_recipe(data, r, on)
            b = to_int(r["byte"])
        except (KeyError, ValueError, TypeError, IndexError) as e:
            messagebox.showerror(APP_NAME, f"This recipe in coding_e90.json is broken: {e}")
            return
        state = "ON" if on else "OFF"
        if new == data:
            messagebox.showinfo(APP_NAME, f"{r.get('name')} is already {state}.")
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"Turn {state}: {r.get('name')}\n\n"
            f"{mod['name']} ({mod['variant']}), byte {b} (0x{b:02X}): 0x{data[b]:02X} -> 0x{new[b]:02X}\n\n"
            "A backup is saved first. Keep the ignition on and don't unplug the cable until it finishes.",
            icon="warning",
        ):
            return
        self._start_write(mod, p, new, "recipe", f"{r.get('name')} {state}")

    def restore_coding(self):
        mod = self._coding_ready()
        if not mod:
            return
        path = filedialog.askopenfilename(title="Coding backup to restore", initialdir=BACKUP_DIR if os.path.isdir(BACKUP_DIR) else HERE,
                                          filetypes=[("Coding backup", "*.json")])
        if not path:
            return
        p, data = self.coding["profile"], self.coding["data"]
        try:
            bk = load_json(path, None)
            new = bytes.fromhex(bk["hex"])
        except Exception:
            messagebox.showerror(APP_NAME, "That file isn't a Bimmer Reader coding backup.")
            return
        problems = []
        if (bk.get("variant") or "").upper() != mod["variant"].upper():
            problems.append(f"it's from variant {bk.get('variant')}, this module is {mod['variant']}")
        if bk.get("profile") != p.get("name"):
            problems.append(f"it's for block '{bk.get('profile')}', not '{p.get('name')}'")
        if len(new) != len(data):
            problems.append(f"it's {len(new)} bytes, the module has {len(data)}")
        if problems:
            messagebox.showerror(APP_NAME, "Can't restore this backup:\n\n- " + "\n- ".join(problems))
            return
        diff = sum(a != b for a, b in zip(new, data))
        if not diff:
            messagebox.showinfo(APP_NAME, "The module already matches this backup.")
            return
        if not messagebox.askyesno(APP_NAME, f"Restore {os.path.basename(path)} to {mod['name']}?\n\n"
                                   f"Saved {bk.get('time', '?')}. {diff} byte(s) will change.", icon="warning"):
            return
        self._start_write(mod, p, new, "restore", f"restore {os.path.basename(path)}")

    def show_coding_jobs(self):
        if not self._need_connection():
            return
        mod = self._selected_module()
        if not mod:
            return

        def work():
            sgbd = self._resolve(mod)
            _, sets = self.backend.job(sgbd, "_JOBS")
            jobs = sorted({fmt(s.get("JOBNAME", "")) for s in sets if s.get("JOBNAME")})
            hits = [j for j in jobs if any(k in j.upper() for k in ("CODIER", "C_C_", "C_S_", "C_FG", "CODING", "CPS"))]
            out = []
            for j in hits:
                info = []
                for meta in ("_JOBCOMMENTS", "_ARGUMENTS"):
                    try:
                        info.append([", ".join(fmt(v) for k, v in s.items() if k != "JOB_STATUS") for s in self.backend.job(sgbd, meta, j)[1]])
                    except DiagError:
                        info.append([])
                out.append((j, *info))
            return out

        def done(res, err):
            if err:
                self.log(f"{mod['name']}: {err}", "err")
                return
            self._refresh_module_tree()
            self._refresh_coding_profiles(mod)
            lines = [f"Coding-related jobs in {mod.get('resolved')} ({mod['variant']}):", ""]
            for job, comments, args in res:
                lines.append(f"{job}  {' '.join(comments)}")
                lines += [f"    arg: {a}" for a in args]
            if not res:
                lines.append("None found. Try the Job runner's full job list.")
            self.coding_text.delete("1.0", "end")
            self.coding_text.insert("end", "\n".join(lines))
            self.log(f"{mod['name']}: {len(res)} coding-related job(s).")

        self.run(work, cb=done)
