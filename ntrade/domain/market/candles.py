"""CandleSeries — domain-typed wrapper around OHLCV DataFrame.

Broker adapters return CandleSeries instead of raw pd.DataFrame, keeping
pandas behind the broker boundary (same pattern as IVSurface / GreeksTable).
DataFrame interop is preserved via to_dataframe() and __getattr__ delegation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    pass


class CandleSeries:
    """OHLCV candle data from a broker — domain-typed wrapper around DataFrame.

    Wraps a DataFrame with columns: timestamp, open, high, low, close, volume.
    Provides domain-typed access while allowing DataFrame escape hatch via
    to_dataframe() and __getattr__ delegation (same pattern as IVSurface).
    """

    def __init__(self, data: pd.DataFrame, *, symbol: str = "", timeframe: str = ""):
        object.__setattr__(self, "_df", data)
        object.__setattr__(self, "_symbol", symbol)
        object.__setattr__(self, "_timeframe", timeframe)

    def to_dataframe(self) -> pd.DataFrame:
        """Escape hatch: return the underlying DataFrame."""
        return self._df

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def timeframe(self) -> str:
        return self._timeframe

    @property
    def empty(self) -> bool:
        return self._df.empty

    def __len__(self) -> int:
        return len(self._df)

    def __getattr__(self, name: str):
        """Delegate unknown attributes to the underlying DataFrame.

        This allows callers to use DataFrame methods (.columns, .iloc, etc.)
        while the canonical API is domain-typed.
        """
        try:
            return getattr(self._df, name)
        except AttributeError:
            raise AttributeError(
                f"CandleSeries has no attribute {name!r} "
                f"(neither on the domain type nor the underlying DataFrame)"
            )

    def __repr__(self) -> str:
        rows = len(self._df) if hasattr(self, "_df") else 0
        return f"CandleSeries(symbol={self._symbol!r}, timeframe={self._timeframe!r}, rows={rows})"
