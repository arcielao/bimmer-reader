"""Simulated 2006 330xi so every screen can be tried without a car."""

from __future__ import annotations

import math
import random
import time

from bimmer.diag import DiagError, clean_value


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
                "battery": {"km": 171204, "count": 1},  # last replacement, as the DME would report it
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
                       "faults": [(0xA3B6, "Demo fault - low beam left, open circuit (simulated data)", "Fault currently present", 7, "")],
                       "coding": bytearray.fromhex("A1 00 3C 02 00 10 00 00 FF 01 00 00 00 08 00 00"
                                                   "00 00 00 00 00 00 00 00 00 00 00 00 5A 00 00 00")},
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

    def job(self, ecu: str, job: str, params: str | bytes = "", raw: bool = False):
        sys_set, sets = self._job(ecu, job, params)
        if raw:
            return sys_set, sets
        return sys_set, [{k: clean_value(v) for k, v in s.items()} for s in sets]

    def _job(self, ecu: str, job: str, params: str | bytes):
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
        if e.get("battery"):
            jobs += ["STEUERN_BATTERIETAUSCH_REGISTRIEREN", "STATUS_BATTERIETAUSCH"]
        if "coding" in e:
            jobs += ["C_C_LESEN", "C_C_SCHREIBEN"]

        if job_u == "_JOBS":
            return sys_set, [{"JOBNAME": j} for j in jobs]
        if job_u in ("_ARGUMENTS", "_JOBCOMMENTS"):
            info = {
                "C_C_LESEN": ([], "Read coding data (demo)"),
                "C_C_SCHREIBEN": ([("CODIERDATEN", "binary", "coding data to write (demo)")], "Write coding data (demo)"),
            }.get(params.upper() if isinstance(params, str) else "", ([], ""))
            if job_u == "_ARGUMENTS":
                return sys_set, [{"ARG": a, "ARGTYPE": t, "ARGCOMMENT": c} for a, t, c in info[0]]
            return sys_set, [{"JOBCOMMENT0": info[1]}] if info[1] else []
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
        if e.get("battery") and job_u == "STEUERN_BATTERIETAUSCH_REGISTRIEREN":
            e["battery"].update(km=183550, count=e["battery"]["count"] + 1)
            return sys_set, [{"JOB_STATUS": "OKAY"}]
        if e.get("battery") and job_u == "STATUS_BATTERIETAUSCH":
            b = e["battery"]
            return sys_set, [{"STAT_KM_BATTERIETAUSCH_WERT": b["km"], "STAT_KM_BATTERIETAUSCH_EINH": "km",
                              "STAT_ANZAHL_BATTERIETAUSCH_WERT": b["count"], "JOB_STATUS": "OKAY"}]
        if "coding" in e and job_u == "C_C_LESEN":
            return sys_set, [{"CODIERDATEN": bytes(e["coding"]), "JOB_STATUS": "OKAY"}]
        if "coding" in e and job_u == "C_C_SCHREIBEN":
            if not isinstance(params, bytes) or len(params) != len(e["coding"]):
                return sys_set, [{"JOB_STATUS": "ERROR_LENGTH"}]
            e["coding"][:] = params
            return sys_set, [{"JOB_STATUS": "OKAY"}]
        if e.get("live") and job_u in self._live():
            return sys_set, [dict(self._live()[job_u], JOB_STATUS="OKAY")]
        raise DiagError(f"SYS-0008: JOB '{job}' NOT FOUND (demo)")
