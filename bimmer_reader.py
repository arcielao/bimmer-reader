#!/usr/bin/env python3
"""
Bimmer Reader - an INPA-style BMW diagnostic tool built on EDIABAS.

Reads and clears fault memory, shows module identification, streams live
values, runs any EDIABAS job (Tool32 style), registers a new battery and
backs up / changes coding on E-series BMWs through a K+DCAN cable.

Requirements (Windows):
  * 32-bit Python 3.10+          (EDIABAS's api32.dll is 32-bit)
  * pip install pydiabas
  * A working EDIABAS install (C:\EDIABAS) with the ECU description files
    (.prg/.grp) for your car - e.g. the one that came with your INPA setup,
    or EdiabasLib's open-source api32.dll pointed at those files.

Run:   python bimmer_reader.py            (real car)
       python bimmer_reader.py --demo     (simulated car, no cable needed)

The code lives in the bimmer package next to this file.
"""

from __future__ import annotations

import argparse
import tkinter as tk

from bimmer.config import APP_NAME
from bimmer.gui.app import App


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
