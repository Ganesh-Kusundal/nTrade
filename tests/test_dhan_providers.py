"""Tests for Dhan provider decomposition: DhanMapper, DhanAuthProvider, DhanTransport."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from ntrade.brokers.dhan_auth_provider import DhanAuthProvider
from ntrade.brokers.dhan_mapper import DhanMapper, to_records, _f
from ntrade.brokers.dhan_transport import DhanTransport
from ntrade.domain.market.quote import Quote
from ntrade.domain.orders.book import OrderBook, TradeBook


# ================================================================ DhanMapper

class TestDhanMapperSymbol:
    def test_equity_returns_symbol(self):
        inst = MagicMock()
        inst.KIND = "equity"
        inst.symbol = "RELIANCE"
        assert DhanMapper.to_trading_symbol(inst) == "RELIANCE"

    def test_option_formats_custom_symbol(self):
        inst = MagicMock()
        inst.KIND = "option"
        inst.option_type = "CE"
        inst.expiry = date(2026, 8, 4)
        inst.strike = 24400.0
        inst.underlying_symbol = "NIFTY"
        result = DhanMapper.to_trading_symbol(inst)
        assert result == "NIFTY 04 AUG 24400 CALL"

    def test_option_put(self):
        inst = MagicMock()
        inst.KIND = "option"
        inst.option_type = "PE"
        inst.expiry = date(2026, 9, 25)
        inst.strike = 15000.0
        inst.underlying_symbol = "BANKNIFTY"
        result = DhanMapper.to_trading_symbol(inst)
        assert result == "BANKNIFTY 25 SEP 15000 PUT"

    def test_option_fractional_strike(self):
        inst = MagicMock()
        inst.KIND = "option"
        inst.option_type = "CE"
        inst.expiry = date(2026, 9, 26)
        inst.strike = 83.2
        inst.underlying_symbol = "USDINR"
        result = DhanMapper.to_trading_symbol(inst)
        assert "83.2" in result
        assert "CALL" in result


class TestDhanMapperTimeframe:
    def test_standard_timeframes(self):
        assert DhanMapper.map_timeframe("5m") == "5"
        assert DhanMapper.map_timeframe("1d") == "DAY"
        assert DhanMapper.map_timeframe("1h") == "60"

    def test_unsupported_raises(self):
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            DhanMapper.map_timeframe("10m")


class TestDhanMapperNormalizeQuote:
    def test_basic_ltp(self):
        q = DhanMapper.normalize_quote(100.0)
        assert isinstance(q, Quote)
        assert q.ltp == 100.0

    def test_with_quote_data(self):
        qd = {"high": 110, "low": 95, "open": 100, "close_price": 99, "volume": 1000, "open_interest": 5000}
        q = DhanMapper.normalize_quote(105.0, qd)
        assert q.high == 110.0
        assert q.volume == 1000
        assert q.oi == 5000


class TestDhanMapperHistory:
    def test_normalize_empty(self):
        df = DhanMapper.normalize_history(None)
        assert df.empty

    def test_normalize_columns(self):
        df = pd.DataFrame({"Timestamp": ["2026-01-01"], "Open": [100], "High": [110],
                           "Low": [90], "Close": [105], "Volume": [1000]})
        result = DhanMapper.normalize_history(df)
        assert list(result.columns) == ["timestamp", "open", "high", "low", "close", "volume"]


class TestDhanMapperOrderBook:
    def test_normalize_orderbook(self):
        records = [{"tradingSymbol": "RELIANCE", "orderId": "123", "transactionType": "BUY",
                     "quantity": 10, "price": 2500.0, "status": "COMPLETE", "exchangeSegment": "NSE"}]
        ob = DhanMapper.normalize_orderbook(records)
        assert isinstance(ob, OrderBook)
        assert len(ob.entries) == 1
        assert ob.entries[0].symbol == "RELIANCE"

    def test_normalize_tradebook(self):
        records = [{"tradingSymbol": "TCS", "tradeId": "T1", "orderId": "O1",
                     "transactionType": "SELL", "quantity": 5, "price": 3800.0}]
        tb = DhanMapper.normalize_tradebook(records)
        assert isinstance(tb, TradeBook)
        assert len(tb.entries) == 1


class TestDhanMapperPortfolio:
    def test_positions_from_empty_df(self):
        assert DhanMapper.positions_from_df(None) == []
        assert DhanMapper.positions_from_df(pd.DataFrame()) == []

    def test_holdings_from_empty_df(self):
        assert DhanMapper.holdings_from_df(None) == []
        assert DhanMapper.holdings_from_df(pd.DataFrame()) == []


# ============================================================ to_records

class TestToRecords:
    def test_none(self):
        assert to_records(None) == []

    def test_dataframe(self):
        df = pd.DataFrame({"a": [1, 2], "b": [3, 4]})
        records = to_records(df)
        assert len(records) == 2
        assert records[0]["a"] == 1

    def test_dict_of_dicts(self):
        d = {"RELIANCE": {"ltp": 2500}}
        records = to_records(d)
        assert len(records) == 1
        assert records[0]["symbol"] == "RELIANCE"

    def test_list(self):
        records = to_records([{"a": 1}, {"a": 2}])
        assert len(records) == 2


class TestScalarHelpers:
    def test_f_converts(self):
        assert _f(10) == 10.0
        assert _f("5.5") == 5.5
        assert _f(None) == 0.0
        assert _f("bad") == 0.0


# ========================================================= DhanAuthProvider

class TestDhanAuthProvider:
    def test_initial_state(self):
        auth = DhanAuthProvider()
        assert auth.tsl is None
        assert auth.is_authenticated is False

    @patch("ntrade.brokers.dhan_auth_provider.get_tradehull")
    def test_authenticate(self, mock_get):
        mock_tsl = MagicMock()
        mock_get.return_value = mock_tsl
        auth = DhanAuthProvider()
        result = auth.authenticate()
        assert result is mock_tsl
        assert auth.tsl is mock_tsl
        assert auth.is_authenticated is True

    def test_repr(self):
        auth = DhanAuthProvider()
        assert "not authenticated" in repr(auth)


# ========================================================= DhanTransport

class TestDhanTransport:
    def test_get_ltp_success(self):
        tsl = MagicMock()
        tsl.get_ltp_data.return_value = {"RELIANCE": 2500.0}
        transport = DhanTransport(tsl)
        assert transport.get_ltp("RELIANCE") == 2500.0

    def test_get_ltp_retry(self):
        tsl = MagicMock()
        tsl.get_ltp_data.side_effect = [Exception("fail"), {"RELIANCE": 2500.0}]
        transport = DhanTransport(tsl)
        assert transport.get_ltp("RELIANCE") == 2500.0
        assert tsl.get_ltp_data.call_count == 2

    def test_get_ltp_all_fail(self):
        tsl = MagicMock()
        tsl.get_ltp_data.side_effect = Exception("fail")
        transport = DhanTransport(tsl)
        assert transport.get_ltp("RELIANCE") == 0.0

    def test_get_balance(self):
        tsl = MagicMock()
        tsl.get_balance.return_value = 500000.0
        transport = DhanTransport(tsl)
        assert transport.get_balance() == 500000.0

    def test_get_lot_size(self):
        tsl = MagicMock()
        tsl.get_lot_size.return_value = 75
        transport = DhanTransport(tsl)
        assert transport.get_lot_size("NIFTY 24500 CALL") == 75

    def test_get_lot_size_error(self):
        tsl = MagicMock()
        tsl.get_lot_size.side_effect = Exception("fail")
        transport = DhanTransport(tsl)
        assert transport.get_lot_size("X") == 0

    def test_get_expiry_list(self):
        tsl = MagicMock()
        tsl.get_expiry_list.return_value = ["2026-08-06", "2026-08-13"]
        transport = DhanTransport(tsl)
        dates = transport.get_expiry_list("NIFTY", "INDEX")
        assert len(dates) == 2
        assert dates[0] == date(2026, 8, 6)

    def test_get_orderbook_records(self):
        tsl = MagicMock()
        tsl.get_orderbook.return_value = pd.DataFrame({
            "tradingSymbol": ["RELIANCE"], "orderId": ["O1"],
            "transactionType": ["BUY"], "quantity": [10],
            "price": [2500.0], "status": ["COMPLETE"],
            "exchangeSegment": ["NSE"],
        })
        transport = DhanTransport(tsl)
        records = transport.get_orderbook()
        assert len(records) == 1

    def test_tsl_setter(self):
        tsl1 = MagicMock()
        tsl2 = MagicMock()
        transport = DhanTransport(tsl1)
        assert transport.tsl is tsl1
        transport.tsl = tsl2
        assert transport.tsl is tsl2


def test_normalize_quote_accepts_now_parameter():
    ts = datetime(2026, 1, 1, 10, 0, 0)
    q = DhanMapper.normalize_quote(100.0, now=ts)
    assert q.timestamp == ts
