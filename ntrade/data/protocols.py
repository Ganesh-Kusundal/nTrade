from typing import Protocol, runtime_checkable
from datetime import datetime
import pandas as pd


@runtime_checkable
class HistoryStorage(Protocol):
    """Protocol for OHLCV storage backends.

    ponytail: ParquetStorage is the only implementation today.
    Add TimescaleDB/InfluxDB by implementing these 5 methods.
    """

    def upsert(self, df: pd.DataFrame) -> int: ...
    def read(
        self,
        symbols: list[str] | None = None,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        timeframe: str | None = None,
    ) -> pd.DataFrame: ...
    def symbols(self) -> list[str]: ...
    def date_range(
        self, symbol: str, timeframe: str | None = None
    ) -> tuple[datetime, datetime] | None: ...
    def clear(self) -> None: ...
