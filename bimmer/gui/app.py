"""Main window: layout, worker plumbing, connection and the module list.

Each tab's widgets and actions live in a mixin in this package."""

from __future__ import annotations

import datetime as dt
import os
import queue
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from bimmer.backends.demo import DemoBackend
from bimmer.backends.ediabas import EdiabasBackend
from bimmer.config import (APP_NAME, APP_VERSION, CODING_FILE, DEFAULT_EDIABAS_DIR, DEFAULT_MODULES, MODULES_FILE,
                           SETTINGS_FILE, load_json, save_json)
from bimmer.diag import DiagError, fmt, is_missing_file_error, parse_faults
from bimmer.gui.battery import BatteryActions
from bimmer.gui.coding_tab import CodingTab
from bimmer.gui.faults_tab import FaultsTab
from bimmer.gui.ident_tab import IdentTab
from bimmer.gui.jobs_tab import JobsTab
from bimmer.gui.live_tab import LiveTab
from bimmer.gui.theme import COLORS
from bimmer.worker import Worker


class App(FaultsTab, IdentTab, LiveTab, JobsTab, CodingTab, BatteryActions):
    """The main window. Tab mixins share its state (self.modules, self.backend, ...) and helpers (run, log, _resolve)."""

    def __init__(self, root: tk.Tk, demo: bool = False, ediabas_dir: str | None = None):
        self.root = root
        self.settings = load_json(SETTINGS_FILE, {})
        self.ediabas_dir = ediabas_dir or self.settings.get("ediabas_dir", DEFAULT_EDIABAS_DIR)
        self.config = load_json(MODULES_FILE, DEFAULT_MODULES)
        self.modules: list[dict] = [dict(m, resolved=None, variant="", state="", faults=None) for m in self.config.get("modules", [])]
        self.demo_var = tk.BooleanVar(value=demo)
        self.backend = None
        self.out_q: queue.Queue = queue.Queue()
        self.worker = Worker(self.out_q)
        self.worker.start()
        self.live_running = False
        self.live_rows: dict[tuple, str] = {}
        self.live_csv = None
        self.live_writer = None
        self.current_jobs: list[str] = []
        self.current_faults: list[dict] = []
        self.coding_cfg = load_json(CODING_FILE, {"profiles": []})
        self.coding: dict | None = None  # last coding read: mod, profile, data

        root.title(f"{APP_NAME} {APP_VERSION}")
        root.geometry("1240x800")
        root.minsize(980, 640)
        root.configure(bg=COLORS["bg"])
        self._style()
        self._build()
        self._refresh_module_tree()
        self._set_connected(False)
        self.log(f"{APP_NAME} {APP_VERSION} ready. Vehicle profile: {self.config.get('vehicle', '?')}.")
        if demo:
            self.log("Demo mode: a simulated 330xi answers instead of the car.")
        else:
            self.log("Order that works best: cable into OBD port -> ignition ON (engine off) -> USB into laptop -> Connect.")
        root.after(50, self._pump)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _style(self):
        s = ttk.Style(self.root)
        try:
            s.theme_use("vista" if sys.platform == "win32" else "clam")
        except tk.TclError:
            s.theme_use("clam")
        if s.theme_use() != "vista":
            s.theme_use("clam")
        base = ("Segoe UI", 10) if sys.platform == "win32" else ("DejaVu Sans", 10)
        self.font = base
        self.mono = ("Consolas", 10) if sys.platform == "win32" else ("DejaVu Sans Mono", 9)
        s.configure(".", font=base)
        s.configure("TFrame", background=COLORS["bg"])
        s.configure("Panel.TFrame", background=COLORS["panel"])
        s.configure("TLabel", background=COLORS["bg"])
        s.configure("Panel.TLabel", background=COLORS["panel"])
        s.configure("Muted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"])
        s.configure("Title.TLabel", background=COLORS["panel"], font=(base[0], 12, "bold"))
        s.configure("Treeview", rowheight=24, font=base)
        s.configure("Treeview.Heading", font=(base[0], 9, "bold"))
        s.configure("Accent.TButton", font=(base[0], 10, "bold"))
        s.configure("TNotebook.Tab", padding=(14, 6))

    def _build(self):
        # Header bar
        hdr = tk.Frame(self.root, bg=COLORS["header"], height=56)
        hdr.pack(fill="x")
        tk.Label(hdr, text=APP_NAME, bg=COLORS["header"], fg="white", font=(self.font[0], 15, "bold")).pack(side="left", padx=(16, 6), pady=10)
        tk.Label(hdr, text=self.config.get("vehicle", ""), bg=COLORS["header"], fg="#9aa0a6", font=(self.font[0], 10)).pack(side="left", pady=10)

        right = tk.Frame(hdr, bg=COLORS["header"])
        right.pack(side="right", padx=12)
        self.ign_lbl = tk.Label(right, text="Ignition: -", bg=COLORS["header"], fg=COLORS["header_fg"], font=self.font)
        self.ign_lbl.pack(side="right", padx=8)
        self.bat_lbl = tk.Label(right, text="Battery: -", bg=COLORS["header"], fg=COLORS["header_fg"], font=self.font)
        self.bat_lbl.pack(side="right", padx=8)
        self.conn_lbl = tk.Label(right, text="  Disconnected  ", bg="#3c4043", fg="white", font=(self.font[0], 9, "bold"))
        self.conn_lbl.pack(side="right", padx=8)

        # Toolbar
        bar = ttk.Frame(self.root, padding=(12, 8))
        bar.pack(fill="x")
        self.connect_btn = ttk.Button(bar, text="Connect", style="Accent.TButton", command=self.toggle_connect)
        self.connect_btn.pack(side="left")
        ttk.Checkbutton(bar, text="Demo mode (no car)", variable=self.demo_var).pack(side="left", padx=12)
        ttk.Button(bar, text="Quick test (all modules)", command=self.quick_test).pack(side="left", padx=4)
        ttk.Button(bar, text="Save full report...", command=self.save_report).pack(side="left", padx=4)
        ttk.Button(bar, text="Battery...", command=self.open_battery).pack(side="left", padx=4)
        ttk.Button(bar, text="Settings", command=self.open_settings).pack(side="right")
        self.busy_lbl = ttk.Label(bar, text="", foreground=COLORS["accent"])
        self.busy_lbl.pack(side="right", padx=12)

        # Main split
        main = ttk.PanedWindow(self.root, orient="horizontal")
        main.pack(fill="both", expand=True, padx=12, pady=(0, 6))
        self.root.after(150, lambda: main.sashpos(0, 460))

        # Left: modules
        left = ttk.Frame(main, style="Panel.TFrame", padding=10)
        main.add(left, weight=1)
        ttk.Label(left, text="Modules", style="Title.TLabel").pack(anchor="w")
        ttk.Label(left, text="Pick a module, then use the tabs on the right.", style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        cols = ("sgbd", "variant", "faults", "state")
        self.mod_tree = ttk.Treeview(left, columns=cols, show="tree headings", selectmode="browse", height=14)
        self.mod_tree.heading("#0", text="Module")
        self.mod_tree.column("#0", width=165, stretch=True)
        for c, t, w in (("sgbd", "SGBD", 62), ("variant", "Variant", 68), ("faults", "Faults", 46), ("state", "Status", 84)):
            self.mod_tree.heading(c, text=t)
            self.mod_tree.column(c, width=w, stretch=False, anchor="w")
        self.mod_tree.tag_configure("ok", foreground=COLORS["ok"])
        self.mod_tree.tag_configure("bad", foreground=COLORS["bad"])
        self.mod_tree.tag_configure("warn", foreground=COLORS["warn"])
        self.mod_tree.pack(fill="both", expand=True)
        self.mod_tree.bind("<<TreeviewSelect>>", lambda e: self._on_module_selected())
        mb = ttk.Frame(left, style="Panel.TFrame")
        mb.pack(fill="x", pady=(6, 0))
        ttk.Button(mb, text="Add module...", command=self.add_module).pack(side="left")
        ttk.Button(mb, text="Remove", command=self.remove_module).pack(side="left", padx=4)

        # Right: tabs
        right_panel = ttk.Frame(main, style="Panel.TFrame", padding=10)
        main.add(right_panel, weight=3)
        self.sel_lbl = ttk.Label(right_panel, text="No module selected", style="Title.TLabel")
        self.sel_lbl.pack(anchor="w", pady=(0, 6))
        self.tabs = ttk.Notebook(right_panel)
        self.tabs.pack(fill="both", expand=True)
        self._build_faults_tab()
        self._build_ident_tab()
        self._build_live_tab()
        self._build_jobs_tab()
        self._build_coding_tab()

        # Log
        logf = ttk.Frame(self.root, padding=(12, 0, 12, 10))
        logf.pack(fill="x")
        self.log_txt = tk.Text(logf, height=6, font=self.mono, bg="#1f2228", fg="#d7dae0", relief="flat", padx=8, pady=6, wrap="word")
        self.log_txt.pack(fill="x")
        self.log_txt.tag_configure("err", foreground="#ff8a80")
        self.log_txt.tag_configure("ok", foreground="#81c995")
        self.log_txt.configure(state="disabled")

    def _tree(self, parent, cols, widths, height=12, show="headings"):
        frame = ttk.Frame(parent, style="Panel.TFrame")
        tree = ttk.Treeview(frame, columns=cols, show=show, height=height)
        for c, w in zip(cols, widths):
            tree.heading(c, text=c)
            tree.column(c, width=w, stretch=(w > 150), anchor="w")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return frame, tree

    def _pump(self):
        try:
            while True:
                cb, res, err = self.out_q.get_nowait()
                if cb:
                    try:
                        cb(res, err)
                    except Exception as e:  # keep the UI alive
                        self.log(f"UI error: {e}", "err")
        except queue.Empty:
            pass
        self.busy_lbl.configure(text="Working..." if (self.worker.busy or self._pending) else "")
        self.root.after(50, self._pump)

    _pending = 0

    def run(self, fn, *args, cb=None):
        self._pending += 1

        def wrapped(res, err):
            self._pending -= 1
            if cb:
                cb(res, err)

        self.worker.submit(fn, *args, cb=wrapped)

    def log(self, msg: str, tag: str | None = None):
        ts = dt.datetime.now().strftime("%H:%M:%S")
        self.log_txt.configure(state="normal")
        self.log_txt.insert("end", f"[{ts}] {msg}\n", (tag,) if tag else ())
        self.log_txt.see("end")
        self.log_txt.configure(state="disabled")

    def _need_connection(self) -> bool:
        if not (self.backend and self.backend.connected):
            messagebox.showinfo(APP_NAME, "Connect first (or tick Demo mode and connect).")
            return False
        return True

    def _selected_module(self, quiet=False) -> dict | None:
        sel = self.mod_tree.selection()
        if not sel:
            if not quiet:
                messagebox.showinfo(APP_NAME, "Pick a module on the left first.")
            return None
        return self.modules[int(sel[0])]

    def _resolve(self, mod: dict) -> str:
        """Find the first SGBD name for this module that EDIABAS knows. Runs in the worker."""
        names = [mod["resolved"]] if mod.get("resolved") else list(mod["sgbd"])
        last = None
        for name in names:
            try:
                sys_set, sets = self.backend.job(name, "IDENT")
                mod["resolved"] = name
                mod["variant"] = fmt(sys_set.get("VARIANTE", ""))
                return name
            except DiagError as e:
                last = e
                if not is_missing_file_error(str(e)):
                    mod["resolved"] = name  # file exists, module just didn't answer
                    raise
        raise last or DiagError("No SGBD names configured.")

    def _set_connected(self, on: bool):
        if on:
            label = "DEMO" if isinstance(self.backend, DemoBackend) else "Connected"
            self.conn_lbl.configure(text=f"  {label}  ", bg=COLORS["warn"] if label == "DEMO" else COLORS["ok"])
            self.connect_btn.configure(text="Disconnect")
        else:
            self.conn_lbl.configure(text="  Disconnected  ", bg="#3c4043")
            self.connect_btn.configure(text="Connect")
            self.bat_lbl.configure(text="Battery: -")
            self.ign_lbl.configure(text="Ignition: -")

    def toggle_connect(self):
        if self.backend and self.backend.connected:
            self.stop_live()
            backend = self.backend
            self.run(backend.disconnect, cb=lambda r, e: (self._set_connected(False), self.log("Disconnected.")))
            return
        self.backend = DemoBackend() if self.demo_var.get() else EdiabasBackend(self.ediabas_dir)
        for m in self.modules:
            m.update(resolved=None, variant="", state="", faults=None)
        self._refresh_module_tree()
        self.log(f"Connecting via {self.backend.label}...")

        def work():
            self.backend.connect()
            info = {}
            for key, ecu, job in (("iface", "TMODE", "LESE_INTERFACE_TYP"), ("ubatt", "UTILITY", "STATUS_UBATT"), ("ign", "UTILITY", "STATUS_ZUENDUNG")):
                try:
                    info[key] = self.backend.job(ecu, job)[1]
                except DiagError as e:
                    info[key] = e
            return info

        def done(info, err):
            if err:
                self.log(f"Connect failed: {err}", "err")
                messagebox.showerror(APP_NAME, str(err))
                self._set_connected(False)
                return
            self._set_connected(True)
            iface = info.get("iface")
            if isinstance(iface, list) and iface:
                self.log(f"EDIABAS is up. Interface: {fmt(iface[0].get('TYP', '?'))}", "ok")
            else:
                self.log(f"EDIABAS is up, but the interface check failed: {iface}", "err")
            self._show_power(info.get("ubatt"), info.get("ign"))

        self.run(work, cb=done)

    def _first_number(self, sets):
        if not isinstance(sets, list):
            return None
        for s in sets:
            for k, v in s.items():
                if k != "JOB_STATUS" and isinstance(v, (int, float)):
                    return v
        return None

    def _show_power(self, ubatt, ign):
        v = self._first_number(ubatt)
        if v is not None:
            volts = v / 1000 if v > 100 else v
            color = COLORS["header_fg"] if volts >= 12.0 else "#fdd663"
            self.bat_lbl.configure(text=f"Battery: {volts:.1f} V", fg=color)
            if volts < 12.0:
                self.log(f"Battery is at {volts:.1f} V - put a charger on before long sessions.", "err")
        i = self._first_number(ign)
        if i is not None:
            self.ign_lbl.configure(text=f"Ignition: {'ON' if i else 'OFF'}")
            if not i:
                self.log("Ignition reads OFF - most modules only answer with the ignition on.", "err")

    def _refresh_module_tree(self):
        # Update rows in place when the list hasn't changed, so the selection
        # (and the tabs showing data for it) isn't reset.
        rebuild = len(self.mod_tree.get_children()) != len(self.modules) or any(
            self.mod_tree.item(str(i), "text") != m["name"] for i, m in enumerate(self.modules) if self.mod_tree.exists(str(i)))
        if rebuild:
            self.mod_tree.delete(*self.mod_tree.get_children())
        for i, m in enumerate(self.modules):
            faults = "" if m["faults"] is None else str(len(m["faults"]))
            tag = {"OK": "ok", "No response": "bad", "Not installed": "warn"}.get(m["state"], "")
            if m["state"] == "OK" and m["faults"]:
                tag = "warn"
            kw = dict(text=m["name"], values=(m.get("resolved") or m["sgbd"][0], m["variant"], faults, m["state"]),
                      tags=(tag,) if tag else ())
            if rebuild:
                self.mod_tree.insert("", "end", iid=str(i), **kw)
            else:
                self.mod_tree.item(str(i), **kw)

    def _on_module_selected(self):
        mod = self._selected_module(quiet=True)
        if not mod:
            return
        extra = f"  ·  {mod['variant']}" if mod["variant"] else ""
        self.sel_lbl.configure(text=f"{mod['name']}{extra}")
        self._fill_faults(mod["faults"] or [], mod["faults"] is not None)
        for t in (self.ident_tree, self.job_tree):
            t.delete(*t.get_children())
        self.current_jobs = []
        self._fill_job_list()
        self._fill_live_list()
        self._refresh_coding_profiles(mod)

    def add_module(self):
        name = simpledialog.askstring(APP_NAME, "Module name (anything you like):", parent=self.root)
        if not name:
            return
        sgbd = simpledialog.askstring(APP_NAME, "SGBD or group file name (e.g. D_0012 or MSV70).\n"
                                      "Look in C:\\EDIABAS\\Ecu for .grp / .prg files.", parent=self.root)
        if not sgbd:
            return
        self.modules.append({"name": name, "sgbd": [s.strip() for s in sgbd.split(",") if s.strip()],
                             "resolved": None, "variant": "", "state": "", "faults": None})
        self._save_modules()
        self._refresh_module_tree()

    def remove_module(self):
        mod = self._selected_module()
        if mod and messagebox.askyesno(APP_NAME, f"Remove '{mod['name']}' from the list?"):
            self.modules.remove(mod)
            self._save_modules()
            self._refresh_module_tree()

    def _save_modules(self):
        self.config["modules"] = [{"name": m["name"], "sgbd": m["sgbd"]} for m in self.modules]
        save_json(MODULES_FILE, self.config)

    def quick_test(self):
        if not self._need_connection():
            return
        self.log("Quick test: asking every module for its ID and fault count...")

        def work():
            out = []
            for i, m in enumerate(self.modules):
                try:
                    sgbd = self._resolve(m)
                    _, sets = self.backend.job(sgbd, "FS_LESEN")
                    m["faults"] = parse_faults(sets)
                    m["state"] = "OK"
                except DiagError as e:
                    m["faults"] = None
                    m["state"] = "Not installed" if is_missing_file_error(str(e)) else "No response"
                    out.append((m["name"], str(e)))
                self.out_q.put((lambda r, e: self._refresh_module_tree(), None, None))
            return out

        def done(problems, err):
            self._refresh_module_tree()
            if err:
                self.log(f"Quick test stopped: {err}", "err")
                return
            ok = [m for m in self.modules if m["state"] == "OK"]
            total = sum(len(m["faults"]) for m in ok)
            self.log(f"Quick test done: {len(ok)}/{len(self.modules)} modules answered, {total} fault(s) stored.", "ok")
            for name, msg in problems:
                self.log(f"  {name}: {msg}")
            self._on_module_selected()

        self.run(work, cb=done)

    def open_settings(self):
        d = filedialog.askdirectory(title="EDIABAS folder (contains bin\\api32.dll and Ecu\\)", initialdir=self.ediabas_dir)
        if d:
            self.ediabas_dir = os.path.normpath(d)
            self.settings["ediabas_dir"] = self.ediabas_dir
            save_json(SETTINGS_FILE, self.settings)
            self.log(f"EDIABAS folder set to {self.ediabas_dir}. Reconnect to use it.")

    def _on_close(self):
        self.stop_live()
        if self.backend and self.backend.connected:
            try:
                self.backend.disconnect()
            except Exception:
                pass
        self.worker.stop()
        self.root.destroy()
