"""IVSurface and GreeksTable — domain types wrapping DataFrame analytics.

OptionChain analytics (iv_surface, greeks_table) currently return raw
pd.DataFrame, leaking pandas across the domain boundary. These wrappers
provide a domain-typed API while preserving DataFrame interop via
to_dataframe() and __getattr__ delegation (same pattern as HistoricalSeries).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    pass


class IVSurface:
    """Implied volatility surface across strikes and expiries.

    Wraps a DataFrame with columns: strike, expiry, type, iv.
    Provides domain-typed access while allowing DataFrame escape hatch.
    """

    def __init__(self, data: pd.DataFrame):
        object.__setattr__(self, "_df", data)

    def to_dataframe(self) -> pd.DataFrame:
        """Escape hatch: return the underlying DataFrame."""
        return self._df

    def __getattr__(self, name: str):
        """Delegate unknown attributes to the underlying DataFrame.

        This allows callers to use DataFrame methods (.empty, .columns, etc.)
        while the canonical API is domain-typed.
        """
        try:
            return getattr(self._df, name)
        except AttributeError:
            raise AttributeError(
                f"IVSurface has no attribute {name!r} "
                f"(neither on the domain type nor the underlying DataFrame)"
            )

    def __repr__(self) -> str:
        rows = len(self._df) if hasattr(self, "_df") else 0
        return f"IVSurface(rows={rows})"

    def __len__(self) -> int:
        return len(self._df)


class GreeksTable:
    """Option greeks across strikes for a chain snapshot.

    Wraps a DataFrame with columns: strike, type, delta, gamma, theta, vega, iv.
    Provides domain-typed access while allowing DataFrame escape hatch.
    """

    def __init__(self, data: pd.DataFrame):
        object.__setattr__(self, "_df", data)

    def to_dataframe(self) -> pd.DataFrame:
        """Escape hatch: return the underlying DataFrame."""
        return self._df

    def __getattr__(self, name: str):
        """Delegate unknown attributes to the underlying DataFrame."""
        try:
            return getattr(self._df, name)
        except AttributeError:
            raise AttributeError(
                f"GreeksTable has no attribute {name!r} "
                f"(neither on the domain type nor the underlying DataFrame)"
            )

    def __repr__(self) -> str:
        rows = len(self._df) if hasattr(self, "_df") else 0
        return f"GreeksTable(rows={rows})"

    def __len__(self) -> int:
        return len(self._df)
