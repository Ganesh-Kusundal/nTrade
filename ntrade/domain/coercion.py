"""Scalar coercion — single source of truth for wire→domain type conversion."""
from __future__ import annotations

from typing import Any

import pandas as pd


def _is_missing(v: Any) -> bool:
    """True for ``None``, empty string, and any NA/NaN (``float('nan')``,
    ``numpy.nan``, ``pandas.NA``).

    Wire frames (e.g. Dhan) carry NaN for missing numerics; a present-but-NaN
    value must never coerce to a literal like ``"nan"`` or ``0`` — callers use
    the empty-string/invalid-result signal to skip bad rows.
    """
    if v is None or v == "":
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def to_float(v: Any, default: float = 0.0) -> float:
    """Coerce a wire value to float, returning default on any failure."""
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def to_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def first_str(d: dict, *keys: str, default: str = "") -> str:
    """Return the first present key from d as str, else default.

    NaN/NA values are treated as missing (return *default*), never stringified
    to ``"nan"``."""
    for k in keys:
        v = d.get(k)
        if not _is_missing(v):
            return str(v)
    return default


def first_float(d: dict, *keys: str, default: float = 0.0) -> float:
    for k in keys:
        v = d.get(k)
        if not _is_missing(v):
            return to_float(v, default)
    return default


def first_int(d: dict, *keys: str, default: int = 0) -> int:
    for k in keys:
        v = d.get(k)
        if not _is_missing(v):
            return to_int(v, default)
    return default
