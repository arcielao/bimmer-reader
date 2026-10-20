"""Paths, app constants and the JSON files the app reads and writes."""

from __future__ import annotations

import json
import os

APP_NAME = "Bimmer Reader"
APP_VERSION = "1.0"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root, next to run.bat
MODULES_FILE = os.path.join(HERE, "modules_e90.json")
CODING_FILE = os.path.join(HERE, "coding_e90.json")
BACKUP_DIR = os.path.join(HERE, "coding_backups")
BATTERY_HISTORY_FILE = os.path.join(HERE, "battery_history.json")
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
