"""Minimal SI-prefix unit handling for numeric answer scoring.

Only prefix scaling within the same base unit is supported (mA <-> A, kHz <-> MHz, ...).
Anything else (e.g. degC vs K, dBm vs W) is treated as incomparable unless strings match.
"""

from __future__ import annotations

from gqb.textmatch import normalize

PREFIXES: dict[str, float] = {
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "": 1.0,
    "k": 1e3,
    "M": 1e6,
    "G": 1e9,
    "T": 1e12,
}

# Base units we know how to scale. Keys are canonical spellings after _canon().
BASES = {"V", "A", "W", "Hz", "s", "ohm", "F", "H", "B", "b", "bps", "Bps", "m", "J", "C"}

_ALIASES = {
    "ω": "ohm",
    "Ω": "ohm",
    "ohms": "ohm",
    "Ohm": "ohm",
    "sec": "s",
    "hz": "Hz",
    "v": "V",
    "a": "A",
    "w": "W",
    "µ": "u",
    "μ": "u",
    "bit": "b",
    "bits": "b",
    "byte": "B",
    "bytes": "B",
    "°C": "degC",
    "°c": "degC",
    "C°": "degC",
    "degc": "degC",
    "oC": "degC",  # NFKC-folded ordinal sign
    "degreesC": "degC",
    "℃": "degC",
    "%": "%",
}


def _canon(unit: str) -> str:
    u = (
        unit.strip()
        .replace("º", "°")
        .replace("℃", "°C")
        .replace("µ", "u")
        .replace("μ", "u")
        .replace("Ω", "ohm")
        .replace("ω", "ohm")
    )
    u = u.replace(" ", "")
    return _ALIASES.get(u, u)


def split_unit(unit: str | None) -> tuple[float, str] | None:
    """'mA' -> (1e-3, 'A'). Returns None for empty or unknown units."""
    if not unit:
        return None
    u = _canon(unit)
    if u in BASES or u in {"degC", "%"}:
        return 1.0, u
    for prefix, scale in PREFIXES.items():
        if prefix and u.startswith(prefix) and u[len(prefix) :] in BASES:
            return scale, u[len(prefix) :]
    # case-insensitive fallback for prefix letters that cannot be ambiguous
    lower_map = {"K": "k", "g": "G"}
    if u and u[0] in lower_map:
        return split_unit(lower_map[u[0]] + u[1:])
    return None


def convert(value: float, from_unit: str | None, to_unit: str | None) -> float | None:
    """Convert value between prefixed forms of the same base unit. None if incomparable."""
    if _canon(from_unit or "") == _canon(to_unit or ""):
        return value
    if not from_unit or not to_unit:
        return None
    a, b = split_unit(from_unit), split_unit(to_unit)
    if a is None or b is None:
        return value if normalize(from_unit) == normalize(to_unit) else None
    if a[1] != b[1]:
        return None
    return value * a[0] / b[0]


def numeric_match(
    pred: float, pred_unit: str | None, gold: float, gold_unit: str | None, rel_tol: float
) -> bool:
    """True if pred (in pred_unit, defaulting to gold_unit) equals gold within rel_tol."""
    converted = convert(pred, pred_unit or gold_unit, gold_unit)
    if converted is None:
        return False
    if gold == 0:
        return abs(converted) <= rel_tol
    if abs(converted - gold) <= rel_tol * abs(gold):
        return True
    # current sign is a direction convention (IOH = -4 mA vs "4 mA sourced")
    base = split_unit(gold_unit)
    return (
        base is not None
        and base[1] == "A"
        and abs(abs(converted) - abs(gold)) <= rel_tol * abs(gold)
    )


def canon_unit(unit: str | None) -> str | None:
    """Canonical spelling for storage (uA, ohm, degC, ...). Keeps unknown units as printed."""
    if not unit:
        return None
    return _canon(unit)
