"""DiagError and helpers for EDIABAS result sets."""

from __future__ import annotations


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
            return fmt(clean_value(s["JOB_STATUS"]))
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
