"""Scalar coercion — single source of truth for wire→domain type conversion."""
from __future__ import annotations
from typing import Any


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
    """Return the first present key from d as str, else default."""
    for k in keys:
        v = d.get(k)
        if v is not None and v != "":
            return str(v)
    return default


def first_float(d: dict, *keys: str, default: float = 0.0) -> float:
    for k in keys:
        v = d.get(k)
        if v is not None and v != "":
            return to_float(v, default)
    return default


def first_int(d: dict, *keys: str, default: int = 0) -> int:
    for k in keys:
        v = d.get(k)
        if v is not None and v != "":
            return to_int(v, default)
    return default
