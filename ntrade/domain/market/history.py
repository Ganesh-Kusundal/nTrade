"""HistoricalSeries — a dataframe that stays attached to its instrument."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

from ntrade.domain.constants import HISTORY_MAX_AGE_MIN
from ntrade.domain.ohlcv import resample as _ohlcv_resample

if TYPE_CHECKING:
    from ntrade.domain.instruments.base import Instrument


class HistoricalSeries:
    """Wraps a pandas DataFrame of OHLCV candles and remains attached to an Instrument.

    Behaves like a dataframe (`series.close`, `series["close"]`, `len(series)`)
    while owning its own cache state, timeframe and fetch lifecycle.

    API note: `cached` and `indicators` are PROPERTIES (not callables) — the
    mission sketch shows `history.cached()` but the property form `history.cached`
    is the deliberate choice here, consistent with `series.cached`.
    """

    def __init__(self, instrument: "Instrument", df: pd.DataFrame | None = None, timeframe: str = "5m", clock=None):
        self.instrument = instrument
        self.timeframe = timeframe
        self._df = df if df is not None else pd.DataFrame()
        self._df_timeframe = timeframe if df is not None else None
        self._cached = df is not None
        self._last_fetched_at: datetime | None = None
        # D-019: injectable clock (callable -> datetime) so is_fresh/fetch
        # follow the kernel clock (replay parity) instead of wall time.
        self.clock = clock or datetime.now

    # ------------------------------------------------------------------ state
    @property
    def df(self) -> pd.DataFrame:
        """Copy-on-write (D-019): external readers get an independent copy so
        nobody can mutate the instrument's cached frame in place (which would
        desync ``_df_timeframe``/``_cached`` and race the feed thread)."""
        return self._df.copy()

    @property
    def cached(self) -> bool:
        return self._cached

    @property
    def last_fetched_at(self) -> datetime | None:
        return self._last_fetched_at

    def is_fresh(self, max_age_minutes: float = HISTORY_MAX_AGE_MIN) -> bool:
        if self._last_fetched_at is None:
            return False
        return (self.clock() - self._last_fetched_at).total_seconds() < max_age_minutes * 60

    # ------------------------------------------------------------------ fetch
    def fetch(
        self,
        timeframe: str | None = None,
        days: int | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        force: bool = False,
    ) -> "HistoricalSeries":
        """Download history through the instrument's broker adapter (lazy, cached)."""
        if timeframe:
            self.timeframe = timeframe
        # The cache is valid only for the timeframe it was fetched with: asking
        # for "1d" right after "5m" must fetch 1d, not serve stale 5m bars.
        cacheable = self._df_timeframe == self.timeframe
        if not force and self._cached and cacheable and self.is_fresh():
            return self
        broker = self.instrument.broker_adapter
        if broker is None:
            raise RuntimeError(f"{self.instrument} has no broker adapter to fetch history")
        result = broker.get_historical(self.instrument, timeframe=self.timeframe, days=days, start=start, end=end)
        df = result.to_dataframe() if hasattr(result, "to_dataframe") else result
        self._df = df if df is not None else pd.DataFrame()
        self._df_timeframe = self.timeframe
        self._cached = not self._df.empty
        self._last_fetched_at = self.clock()
        return self

    def __call__(self, timeframe=None, days=None, start=None, end=None, force: bool = False) -> "HistoricalSeries":
        """Call form: nifty.history("5m", days=20) fetches and returns this series."""
        return self.fetch(timeframe=timeframe, days=days, start=start, end=end, force=force)

    def refresh(self, **kwargs) -> "HistoricalSeries":
        return self.fetch(force=True, **kwargs)

    def download(self, **kwargs) -> "HistoricalSeries":
        """Force a fresh download, ignoring cache."""
        return self.fetch(force=True, **kwargs)

    # ------------------------------------------------------------------ merge
    def live_merge(self, tick_df: pd.DataFrame | None = None) -> "HistoricalSeries":
        """Merge live ticks into candles (in-place, OHLCV-safe).

        Raw ticks (symbol/price/quantity/...) are converted into single-print
        candle rows before being merged, keeping the frame schema intact.
        """
        tick_df = tick_df if tick_df is not None else self.instrument._stream.live_ticks_df
        if tick_df is None or tick_df.empty or self._df.empty or "timestamp" not in tick_df:
            return self
        rows = []
        for _, t in tick_df.iterrows():
            try:
                price = float(t.get("price", 0.0) or 0.0)
                qty = int(t.get("quantity", 0) or 0)
                ts = pd.to_datetime(t.get("timestamp"))
            except Exception:
                continue
            rows.append({"timestamp": ts, "open": price, "high": price, "low": price, "close": price, "volume": qty})
        if not rows:
            return self
        tick_candles = pd.DataFrame(rows)
        merged = pd.concat([self._df, tick_candles], ignore_index=True)
        self._df = merged.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp").reset_index(drop=True)
        return self

    def resample(self, rule: str) -> "HistoricalSeries":
        """Resample to a coarser timeframe; returns a new HistoricalSeries."""
        if self._df.empty or "timestamp" not in self._df:
            return HistoricalSeries(self.instrument, self._df, self.timeframe)
        # K-025: closed-LABEL/RIGHT-edge labels via the canonical primitive
        # (day_group=False → epoch-anchored, the HistoricalSeries convention).
        resampled = _ohlcv_resample(self._df, rule, day_group=False)
        return HistoricalSeries(self.instrument, resampled, rule, clock=self.clock)
    # ------------------------------------------------------------------ pandas
    def indicators(self, **params) -> dict[str, float]:
        """Compute the indicator bundle over this series (mission: history.indicators())."""
        from ntrade.domain.analytics.indicators import compute_bundle
        return compute_bundle(self._df, **params)

    def to_df(self) -> pd.DataFrame:
        return self._df.copy()

    def __len__(self) -> int:
        return len(self._df)

    def __getitem__(self, key):
        # NOTE (D-019): column/row views into the internal frame are returned
        # as-is for performance — mutating them in place would corrupt the
        # cache. Use ``df`` / ``to_df()`` when you need a copy-safe surface.
        return self._df[key]

    def __getattr__(self, name):
        # Delegate dataframe-like attribute access (e.g. series.close, .iloc).
        # Same CoW note as __getitem__: these are views into the internal
        # frame — read them, don't mutate in place.
        try:
            return getattr(self._df, name)
        except AttributeError:
            raise AttributeError(f"HistoricalSeries has no attribute {name!r}") from None

    def __repr__(self) -> str:
        return f"HistoricalSeries({self.instrument.symbol}, tf={self.timeframe}, rows={len(self._df)}, cached={self._cached})"
