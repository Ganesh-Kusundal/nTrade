"""Protocols for instrument type hints — breaks the base ↔ capabilities ↔ chain cycle."""

from __future__ import annotations

from typing import Protocol


class InstrumentProtocol(Protocol):
    symbol: str
    exchange: str
    KIND: str
