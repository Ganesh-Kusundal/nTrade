"""Universe — load and map Nifty index constituent CSVs to Equity instruments.

CSV columns: Company Name, Industry, Symbol, Series, ISIN Code
Maps the ``Symbol`` column to :class:`ntrade.domain.instruments.cash.Equity`
via :class:`ntrade.factories.InstrumentFactory`.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from ntrade.factories import InstrumentFactory

# Standard NSE equity exchange
_NSE = "NSE"

# Default locations relative to project root
_DEFAULT_CSV_DIR = Path(__file__).resolve().parent.parent.parent / "Dependencies"

# NSE series mapping: EQ (cash), BE (be) → cash; others mapped as needed
# ponytail: EQ is the only delivery-series we support for historical OHLCV today
_CASH_SERIES = frozenset({"EQ", "BE"})


def _load_csv(path: Path) -> list[dict[str, str]]:
    """Read a Nifty constituent CSV into a list of row dicts."""
    rows: list[dict[str, str]] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(row)
    return rows


def load_universe(name: str = "nifty50",
                  csv_dir: Path | str | None = None,
                  broker=None) -> list:
    """Load a Nifty index constituents CSV and return Equity instruments.

    Args:
        name: ``nifty50``, ``nifty100``, ``nifty200``, or ``nifty500``.
        csv_dir: Directory containing the CSV files (default: ``<repo>/Dependencies``).
        broker: Optional BrokerAdapter to attach to each instrument.

    Returns:
        A list of :class:`Equity` instruments, one per constituent.
    """
    csv_dir = Path(csv_dir) if csv_dir else _DEFAULT_CSV_DIR
    csv_path = csv_dir / f"{name}_list.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"Universe CSV not found: {csv_path}")

    factory = InstrumentFactory(broker=broker)
    instruments: list = []
    for row in _load_csv(csv_path):
        symbol = row["Symbol"].strip().upper()
        # Skip non-cash series (e.g. pre-IPO, suspension) — only EQ/BE trade on NSE cash
        series = row.get("Series", "").strip().upper()
        if series not in _CASH_SERIES:
            continue
        instruments.append(factory.equity(symbol, exchange=_NSE))
    return instruments


def available_universes(csv_dir: Path | str | None = None) -> list[str]:
    """Return the names of all universe CSVs available on disk."""
    csv_dir = Path(csv_dir) if csv_dir else _DEFAULT_CSV_DIR
    return sorted(
        p.stem.removesuffix("_list")
        for p in csv_dir.glob("nifty*_list.csv")
    )
