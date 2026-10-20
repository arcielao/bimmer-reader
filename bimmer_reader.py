#!/usr/bin/env python3
"""
Bimmer Reader - an INPA-style BMW diagnostic tool built on EDIABAS.

Reads and clears fault memory, shows module identification, streams live
values and runs any EDIABAS job (Tool32 style) on E-series BMWs through a
K+DCAN cable.

Requirements (Windows):
  * 32-bit Python 3.10+          (EDIABAS's api32.dll is 32-bit)
  * pip install pydiabas
  * A working EDIABAS install (C:\\EDIABAS) with the ECU description files
    (.prg/.grp) for your car - e.g. the one that came with your INPA setup,
    or EdiabasLib's open-source api32.dll pointed at those files.

Run:   python bimmer_reader.py            (real car)
       python bimmer_reader.py --demo     (simulated car, no cable needed)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import queue
import random
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

APP_NAME = "Bimmer Reader"
APP_VERSION = "1.0"
HERE = os.path.dirname(os.path.abspath(__file__))
MODULES_FILE = os.path.join(HERE, "modules_e90.json")
SETTINGS_FILE = os.path.join(HERE, "bimmer_reader_settings.json")
DEFAULT_EDIABAS_DIR = r"C:\EDIABAS"

DEFAULT_MODULES = {
    "vehicle": "E90 330xi (2006)",
    "modules": [
        {"name": "DME - Engine", "sgbd": ["D_0012", "D_MOTOR"]},
        {"name": "EGS - Transmission", "sgbd": ["D_0018", "D_EGS"]},
        {"name": "DSC - Stability / ABS", "sgbd": ["D_0029", "D_ABSKWP"]},
        {"name": "ACSM - Airbag", "sgbd": ["D_0001", "D_MRS"]},
        {"name": "CAS - Car access", "sgbd": ["D_0040", "D_CAS"]},
        {"name": "KOMBI - Instrument cluster", "sgbd": ["D_0060", "D_KOMBI"]},
        {"name": "JBE - Junction box", "sgbd": ["D_0000", "D_JBBF"]},
        {"name": "FRM - Footwell / lights", "sgbd": ["D_0072", "D_FRM"]},
    ],
}


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
class DiagError(Exception):
    """Any failure talking to EDIABAS or the car."""


def clean_value(v):
    """EDIABAS hands back bytes for text and binary results; make them readable."""
    if isinstance(v, (bytes, bytearray)):
        b = bytes(v).rstrip(b"\x00")
        if all(32 <= c < 127 or c in (9, 10, 13) or c >= 160 for c in b):
            return b.decode("latin-1")
        return b.hex(" ").upper()
    if isinstance(v, float):
        return round(v, 4)
    return v


def fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def job_status(sets: list[dict]) -> str:
    """BMW SGBDs report success in a JOB_STATUS result (usually 'OKAY')."""
    for s in reversed(sets):
        if "JOB_STATUS" in s:
            return fmt(s["JOB_STATUS"])
    return ""


def is_missing_file_error(msg: str) -> bool:
    m = msg.upper()
    return "SYS-0002" in m or "NOT FOUND" in m or "SYS-0005" in m


def format_code(v) -> str:
    if isinstance(v, int):
        return f"{v:04X}"
    s = fmt(v).strip()
    if s.lower().startswith("0x"):
        s = s[2:]
    return s.upper()


def parse_faults(sets: list[dict]) -> list[dict]:
    """Turn FS_LESEN result sets into a flat list of faults."""
    faults = []
    for s in sets:
        if "F_ORT_NR" not in s:
            continue

        def first(*keys):
            for k in keys:
                if k in s and fmt(s[k]) not in ("", "--", "-"):
                    return fmt(s[k])
            return ""

        faults.append(
            {
                "code": format_code(s["F_ORT_NR"]),
                "text": first("F_ORT_TEXT", "F_TEXT"),
                "status": first("F_VORHANDEN_TEXT", "F_SYMPTOM_TEXT", "F_ART1_TEXT", "F_LZ_TEXT"),
                "count": first("F_HFK", "F_HAEUFIGKEIT", "F_UEBERLAUF"),
                "pcode": first("F_PCODE_STRING", "F_PCODE_TEXT", "F_PCODE"),
                "raw": s,
            }
        )
    return faults


def pair_units(sets: list[dict]) -> list[tuple[str, object, str]]:
    """Flatten job results into (name, value, unit), joining *_WERT with *_EINH."""
    rows = []
    for s in sets:
        for name, value in s.items():
            if name in ("JOB_STATUS", "JOBNAME") or name.endswith("_EINH"):
                continue
            unit = ""
            if name.endswith("_WERT"):
                unit = fmt(s.get(name[:-5] + "_EINH", ""))
            elif name + "_EINH" in s:
                unit = fmt(s[name + "_EINH"])
            rows.append((name, value, unit))
    return rows


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
#  Backends
# --------------------------------------------------------------------------- #
class EdiabasBackend:
    """Real car: EDIABAS api32.dll through pydiabas."""

    label = "EDIABAS"

    def __init__(self, ediabas_dir: str):
        self.ediabas_dir = ediabas_dir
        self._pd = None

    def connect(self):
        if sys.platform != "win32":
            raise DiagError("EDIABAS only runs on Windows. Use --demo to try the app here.")
        if struct.calcsize("P") * 8 != 32:
            raise DiagError(
                "This is 64-bit Python. EDIABAS needs 32-bit Python - install the "
                "'Windows installer (32-bit)' from python.org and run the app with it."
            )
        bin_dir = os.path.join(self.ediabas_dir, "bin")
        if not os.path.isfile(os.path.join(bin_dir, "api32.dll")):
            raise DiagError(f"api32.dll not found in {bin_dir}. Check the EDIABAS folder in Settings.")
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
        try:
            os.add_dll_directory(bin_dir)
        except Exception:
            pass
        try:
            from pydiabas import PyDIABAS  # loads api32.dll on import
        except ImportError:
            raise DiagError("pydiabas isn't installed. Run:  py -3-32 -m pip install pydiabas")
        except Exception as e:
            raise DiagError(f"Couldn't load api32.dll: {e}")
        try:
            self._pd = PyDIABAS()
            self._pd.start()
        except Exception as e:
            self._pd = None
            raise DiagError(f"EDIABAS failed to start: {e}")

    def disconnect(self):
        if self._pd is not None:
            try:
                self._pd.end()
            finally:
                self._pd = None

    @property
    def connected(self):
        return self._pd is not None

    def job(self, ecu: str, job: str, params: str = "") -> tuple[dict, list[dict]]:
        if self._pd is None:
            raise DiagError("Not connected.")
        try:
            result = self._pd.job(ecu, job, parameters=params)
        except Exception as e:
            raise DiagError(str(e) or e.__class__.__name__)
        dicts = [{k: clean_value(v) for k, v in d.items()} for d in result.as_dicts()]
        if not dicts:
            return {}, []
        return dicts[0], dicts[1:]


class DemoBackend:
    """Simulated 2006 330xi so every screen can be tried without a car."""

    label = "DEMO"

    def __init__(self):
        self._on = False
        self._t0 = time.time()
        self.ecus = {
            "D_0012": {
                "variant": "MSV70",
                "ident": {"ID_BMW_NR": "7563046", "ID_HW_NR": "0261S04", "ID_SW_NR": "DEMO-SW", "ID_DATUM": "KW 32/2005", "ID_LIEF_TEXT": "Siemens (demo)"},
                "faults": [
                    (0x2A82, "Demo fault - camshaft timing, intake (simulated data)", "Fault currently present", 4, "P0011"),
                    (0x2E81, "Demo fault - thermostat warm-up (simulated data)", "Fault stored, not present", 1, "P0128"),
                ],
                "live": True,
            },
            "D_0029": {
                "variant": "DSC_87",
                "ident": {"ID_BMW_NR": "6782981", "ID_SW_NR": "DEMO-SW", "ID_LIEF_TEXT": "Continental Teves (demo)"},
                "faults": [(0x5DF0, "Demo fault - wheel speed sensor rear left (simulated data)", "Fault stored, not present", 2, "")],
            },
            "D_0060": {"variant": "KOMBI87", "ident": {"ID_BMW_NR": "9166819", "ID_SW_NR": "DEMO-SW"}, "faults": []},
            "D_0040": {"variant": "CAS3", "ident": {"ID_BMW_NR": "6943790", "ID_SW_NR": "DEMO-SW"}, "faults": []},
            "D_0000": {"variant": "JBBF87", "ident": {"ID_BMW_NR": "9147228", "ID_SW_NR": "DEMO-SW"},
                       "faults": [(0x9CBC, "Demo fault - wiper relay (simulated data)", "Fault stored, not present", 1, "")]},
            "D_0001": {"variant": "ACSM", "ident": {"ID_BMW_NR": "9184432", "ID_SW_NR": "DEMO-SW"}, "faults": []},
            "D_0018": {"variant": "GS19D", "ident": {"ID_BMW_NR": "7566376", "ID_SW_NR": "DEMO-SW"}, "faults": []},
            "D_0072": {"variant": "FRM_87", "ident": {"ID_BMW_NR": "9134549", "ID_SW_NR": "DEMO-SW"},
                       "faults": [(0xA3B6, "Demo fault - low beam left, open circuit (simulated data)", "Fault currently present", 7, "")]},
            "D_0078": {"variant": "IHKA87", "ident": {"ID_BMW_NR": "9182287", "ID_SW_NR": "DEMO-SW"}, "faults": []},
        }
        # Modules that "exist" as files but don't answer (like an option the car doesn't have)
        self.silent = {"D_0064", "D_0063", "D_0002", "D_0019"}

    def connect(self):
        time.sleep(0.3)
        self._on = True

    def disconnect(self):
        self._on = False

    @property
    def connected(self):
        return self._on

    def _live(self):
        t = time.time() - self._t0
        rpm = 780 + 40 * math.sin(t * 1.3) + random.uniform(-15, 15)
        return {
            "STATUS_MOTORDREHZAHL": {"STAT_MOTORDREHZAHL_WERT": round(rpm, 0), "STAT_MOTORDREHZAHL_EINH": "1/min"},
            "STATUS_MOTORTEMPERATUR": {"STAT_MOTORTEMPERATUR_WERT": round(min(92.0, 40 + t * 0.8), 1), "STAT_MOTORTEMPERATUR_EINH": "degC"},
            "STATUS_ANSAUGLUFTTEMPERATUR": {"STAT_ANSAUGLUFTTEMPERATUR_WERT": round(24 + random.uniform(-0.4, 0.4), 1), "STAT_ANSAUGLUFTTEMPERATUR_EINH": "degC"},
            "STATUS_UBATT": {"STAT_UBATT_WERT": round(14.1 + random.uniform(-0.05, 0.05), 2), "STAT_UBATT_EINH": "V"},
            "STATUS_LUFTMASSE": {"STAT_LUFTMASSE_WERT": round(16 + random.uniform(-0.8, 0.8), 1), "STAT_LUFTMASSE_EINH": "kg/h"},
            "STATUS_LAMBDA_BANK1": {"STAT_LAMBDA_BANK1_WERT": round(1.0 + random.uniform(-0.02, 0.02), 3), "STAT_LAMBDA_BANK1_EINH": "-"},
        }

    def job(self, ecu: str, job: str, params: str = ""):
        if not self._on:
            raise DiagError("Not connected.")
        time.sleep(0.08)
        ecu_u, job_u = ecu.upper(), job.upper()
        sys_set = {"OBJECT": ecu.lower(), "JOBNAME": job_u, "VARIANTE": ecu_u, "JOBSTATUS": ""}

        if ecu_u == "TMODE" and job_u == "LESE_INTERFACE_TYP":
            return sys_set, [{"TYP": "OBD (demo)"}]
        if ecu_u == "UTILITY":
            if job_u == "STATUS_UBATT":
                return sys_set, [{"STAT_UBATT": 12480, "JOB_STATUS": "OKAY"}]
            if job_u == "STATUS_ZUENDUNG":
                return sys_set, [{"STAT_ZUENDUNG": 1, "JOB_STATUS": "OKAY"}]
        if ecu_u in self.silent:
            raise DiagError("IFH-0009: ECU DOESN'T RESPOND (demo)")
        if ecu_u not in self.ecus:
            raise DiagError(f"SYS-0002: ECU OBJECT '{ecu}' NOT FOUND (demo)")

        e = self.ecus[ecu_u]
        sys_set["VARIANTE"] = e["variant"]
        jobs = ["INFO", "IDENT", "FS_LESEN", "FS_LOESCHEN", "IS_LESEN", "IS_LOESCHEN", "SERIENNUMMER_LESEN"]
        if e.get("live"):
            jobs += list(self._live().keys())

        if job_u == "_JOBS":
            return sys_set, [{"JOBNAME": j} for j in jobs]
        if job_u == "INFO":
            return sys_set, [{"ECU": e["variant"], "ORIGIN": "demo", "REVISION": "1.0", "JOB_STATUS": "OKAY"}]
        if job_u == "IDENT":
            return sys_set, [dict(e["ident"], JOB_STATUS="OKAY")]
        if job_u == "FS_LESEN":
            sets = [
                {"F_ORT_NR": c, "F_ORT_TEXT": t, "F_VORHANDEN_TEXT": st, "F_HFK": n, "F_PCODE_STRING": p,
                 "F_UW_KM": 182340 + i * 1210, "F_UW_KM_EINH": "km"}
                for i, (c, t, st, n, p) in enumerate(e["faults"])
            ]
            sets.append({"JOB_STATUS": "OKAY"})
            return sys_set, sets
        if job_u == "FS_LOESCHEN":
            e["faults"] = []
            return sys_set, [{"JOB_STATUS": "OKAY"}]
        if job_u == "SERIENNUMMER_LESEN":
            return sys_set, [{"SERIENNUMMER": "DEMO12345", "JOB_STATUS": "OKAY"}]
        if job_u in ("IS_LESEN", "IS_LOESCHEN"):
            return sys_set, [{"JOB_STATUS": "OKAY"}]
        if e.get("live") and job_u in self._live():
            return sys_set, [dict(self._live()[job_u], JOB_STATUS="OKAY")]
        raise DiagError(f"SYS-0008: JOB '{job}' NOT FOUND (demo)")


# --------------------------------------------------------------------------- #
#  Worker thread - EDIABAS is not thread-safe, so every call goes through here
# --------------------------------------------------------------------------- #
class Worker(threading.Thread):
    def __init__(self, out_q: queue.Queue):
        super().__init__(daemon=True)
        self.in_q: queue.Queue = queue.Queue()
        self.out_q = out_q

    def submit(self, fn, *args, cb=None):
        self.in_q.put((fn, args, cb))

    def stop(self):
        self.in_q.put((None, (), None))

    def run(self):
        while True:
            fn, args, cb = self.in_q.get()
            if fn is None:
                break
            try:
                res, err = fn(*args), None
            except Exception as e:  # noqa: BLE001 - report everything to the UI
                res, err = None, e
            self.out_q.put((cb, res, err))

    @property
    def busy(self):
        return not self.in_q.empty()


# --------------------------------------------------------------------------- #
#  GUI
# --------------------------------------------------------------------------- #
COLORS = {
    "bg": "#f4f5f7",
    "panel": "#ffffff",
    "header": "#16191f",
    "header_fg": "#e8eaed",
    "muted": "#6b7280",
    "accent": "#1c69d4",
    "ok": "#1e8e3e",
    "warn": "#b06000",
    "bad": "#c5221f",
    "line": "#dfe1e5",
}


class App:
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

    # ---------------- style & layout ---------------- #
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

    def _build_ident_tab(self):
        tab = ttk.Frame(self.tabs, style="Panel.TFrame", padding=8)
        self.tabs.add(tab, text="Identification")
        bar = ttk.Frame(tab, style="Panel.TFrame")
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Read identification", style="Accent.TButton", command=self.read_ident).pack(side="left")
        frame, self.ident_tree = self._tree(tab, ("Field", "Value"), (220, 500), height=16)
        frame.pack(fill="both", expand=True)

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

    # ---------------- plumbing ---------------- #
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

    # ---------------- connection ---------------- #
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

    # ---------------- modules ---------------- #
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

    # ---------------- faults ---------------- #
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

    # ---------------- ident ---------------- #
    def read_ident(self):
        if not self._need_connection():
            return
        mod = self._selected_module()
        if not mod:
            return

        def work():
            sgbd = self._resolve(mod)
            sys_set, sets = self.backend.job(sgbd, "IDENT")
            return sys_set, sets

        def done(res, err):
            self.ident_tree.delete(*self.ident_tree.get_children())
            if err:
                self.log(f"{mod['name']}: {err}", "err")
                return
            sys_set, sets = res
            self.ident_tree.insert("", "end", values=("SGBD variant", fmt(sys_set.get("VARIANTE", ""))))
            for s in sets:
                for k, v in s.items():
                    self.ident_tree.insert("", "end", values=(k, fmt(v)))
            mod["state"] = "OK"
            self._refresh_module_tree()
            self.log(f"{mod['name']}: identification read.", "ok")

        self.run(work, cb=done)

    # ---------------- job lists ---------------- #
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

    def load_live_jobs(self):
        if self._need_connection() and (mod := self._selected_module()):
            self._fetch_jobs(mod, self._fill_live_list)

    def _fill_job_list(self):
        f = self.job_filter.get().upper()
        self.job_list.delete(0, "end")
        for j in self.current_jobs:
            if f in j.upper():
                self.job_list.insert("end", j)

    def _fill_live_list(self):
        f = self.live_filter.get().upper()
        self.live_list.delete(0, "end")
        for j in self.current_jobs:
            if j.upper().startswith("STATUS_") and f in j.upper():
                self.live_list.insert("end", j)

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

    # ---------------- live data ---------------- #
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

    # ---------------- settings & shutdown ---------------- #
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--demo", action="store_true", help="simulate a car (no cable needed)")
    ap.add_argument("--ediabas", help=r"EDIABAS folder (default C:\EDIABAS)")
    args = ap.parse_args(argv)
    root = tk.Tk()
    App(root, demo=args.demo, ediabas_dir=args.ediabas)
    root.mainloop()


if __name__ == "__main__":
    main()
