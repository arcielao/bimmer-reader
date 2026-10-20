"""Coding data helpers: profile args, hex dumps and recipe bit-flips. No I/O."""

from __future__ import annotations


from bimmer.diag import DiagError, clean_value, fmt


def build_args(spec: str, data: bytes = b"") -> str | bytes:
    """Coding profile args: 'hex:...' becomes one binary argument; {data} is the coding bytes as hex."""
    if spec.lower().startswith("hex:"):
        return bytes.fromhex(spec[4:].replace("{data}", data.hex()))
    return spec.replace("{data}", data.hex().upper())


def coding_bytes(sets: list[dict], result: str) -> bytes:
    """Pull the coding block out of raw result sets (binary, or a hex string on some SGBDs)."""
    for s in sets:
        if result in s:
            v = s[result]
            if isinstance(v, (bytes, bytearray)):
                return bytes(v)
            if isinstance(v, str):
                try:
                    return bytes.fromhex(v)
                except ValueError:
                    pass
            raise DiagError(f"Result {result} isn't binary coding data: {fmt(clean_value(v))[:60]}")
    raise DiagError(f"The read job didn't return a {result} result.")


def hex_dump(data: bytes) -> str:
    return "\n".join(f"{i:04X}  " + " ".join(f"{b:02X}" for b in data[i:i + 16]) for i in range(0, len(data), 16))


def to_int(v) -> int:
    return int(v, 0) if isinstance(v, str) else int(v)


def recipe_state(data: bytes, r: dict) -> str:
    try:
        b, mask = to_int(r["byte"]), to_int(r["mask"])
        if b >= len(data):
            return "n/a"
        v = data[b] & mask
        if v == to_int(r["on"]) & mask:
            return "ON"
        if v == to_int(r["off"]) & mask:
            return "OFF"
        return f"0x{v:02X}"
    except (KeyError, ValueError, TypeError):
        return "bad recipe"


def apply_recipe(data: bytes, r: dict, on: bool) -> bytes:
    b, mask = to_int(r["byte"]), to_int(r["mask"])
    val = to_int(r["on"] if on else r["off"])
    new = bytearray(data)
    new[b] = (new[b] & ~mask & 0xFF) | (val & mask)
    return bytes(new)
