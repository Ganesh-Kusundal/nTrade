from ntrade.data.protocols import HistoryStorage
from ntrade.data.parquet_store import ParquetStorage
import tempfile
import pandas as pd
from datetime import datetime


def test_parquet_storage_satisfies_protocol():
    with tempfile.TemporaryDirectory() as tmp:
        store = ParquetStorage(tmp)
        assert isinstance(store, HistoryStorage)


def test_protocol_has_required_methods():
    assert hasattr(HistoryStorage, 'upsert')
    assert hasattr(HistoryStorage, 'read')
    assert hasattr(HistoryStorage, 'symbols')
    assert hasattr(HistoryStorage, 'date_range')
    assert hasattr(HistoryStorage, 'clear')
