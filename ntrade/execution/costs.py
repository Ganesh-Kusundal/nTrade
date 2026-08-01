"""Cost models — slippage, commission and Indian-market statutory charges.

Lives in ``execution/`` (not ``backtest/``) because simulated execution is
shared by live paper trading, replay and backtest — zero parity demands the
same cost pipeline everywhere.

Statutory costs (H6): STT, exchange transaction charges, SEBI fee, GST on
brokerage and stamp duty are what make Indian backtest PnL diverge from live.
They are modeled as an additive ``IndianStatutoryCosts`` component that wraps a
commission model; ``total_cost(notional, side, product)`` returns the full
charge so callers can report realised PnL accurately.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SlippageModel(ABC):
    @abstractmethod
    def apply(self, price: float, side: str) -> float:
        """Return the executed price for a BUY/SELL order."""


class FixedSlippage(SlippageModel):
    def __init__(self, points: float = 0.0):
        self.points = points

    def apply(self, price: float, side: str) -> float:
        return price + self.points if side == "BUY" else price - self.points


class PercentageSlippage(SlippageModel):
    def __init__(self, pct: float = 0.0):
        self.pct = pct

    def apply(self, price: float, side: str) -> float:
        return price * (1 + self.pct) if side == "BUY" else price * (1 - self.pct)


class CommissionModel(ABC):
    @abstractmethod
    def apply(self, notional: float) -> float:
        """Commission charged on an order notional."""


class FlatCommission(CommissionModel):
    def __init__(self, amount: float = 0.0):
        self.amount = amount

    def apply(self, notional: float) -> float:
        return self.amount


class PercentageCommission(CommissionModel):
    def __init__(self, pct: float = 0.0, minimum: float = 0.0):
        self.pct = pct
        self.minimum = minimum

    def apply(self, notional: float) -> float:
        return max(notional * self.pct, self.minimum)


# ---------------------------------------------------------------------------
# Indian-market statutory costs (H6)
# ---------------------------------------------------------------------------

class IndianStatutoryCosts:
    """STT / exchange txn charges / SEBI fee / GST / stamp duty for NSE/BSE/MCX.

    Rates are configurable (defaults approximate the 2026 exchange schedule)
    and additive, so a backtest with these charges converges on live PnL:

      - STT: 0.1% on equity delivery sell (0.025% buy), 0.125% on F&O sell;
        intraday equity sell 0.025%, F&O buy 0.0625%
      - Exchange txn charge: 0.00297% equity / 0.00173% futures / 0.0503% options
        (premium) + 0.0001% options exercised (SEBI) — approximated as
        exchange_charge_pct on notional
      - SEBI fee: 0.0001% (₹10/crore)
      - GST: 18% on (brokerage + exchange charge + SEBI fee)
      - Stamp duty: 0.003% equity buy (delivery) / 0.002% intraday equity /
        0.01% F&O
    """

    STT = {
        "equity_delivery_buy": 0.00025,
        "equity_delivery_sell": 0.001,
        "equity_intraday_sell": 0.00025,
        "fno_buy": 0.000625,
        "fno_sell": 0.00125,
    }
    EXCHANGE_CHARGE = {"equity": 0.0000297, "futures": 0.0000173, "options": 0.000503}
    SEBI_FEE = 0.000001
    GST_RATE = 0.18
    STAMP_DUTY = {"equity_delivery_buy": 0.00003, "equity_intraday": 0.00002, "fno": 0.0001}

    def __init__(
        self,
        *,
        product: str = "equity",      # equity | futures | options
        delivery: bool = False,       # equity delivery vs intraday
        brokerage: float = 0.0,       # actual brokerage charged on this order
        stt: dict | None = None,
        exchange_charge: dict | None = None,
        sebi_fee: float | None = None,
        gst_rate: float | None = None,
        stamp_duty: dict | None = None,
    ):
        self.product = product
        self.delivery = delivery
        self.brokerage = brokerage
        # NOTE: stored under private names so the dicts do not shadow the
        # public ``stt()``/``exchange_charge()`` methods.
        self._stt_rates = stt or self.STT
        self._exchange_rates = exchange_charge or self.EXCHANGE_CHARGE
        self.sebi_fee = sebi_fee if sebi_fee is not None else self.SEBI_FEE
        self.gst_rate = gst_rate if gst_rate is not None else self.GST_RATE
        self._stamp_rates = stamp_duty or self.STAMP_DUTY

    def stt(self, notional: float, side: str) -> float:
        """Securities Transaction Tax on the trade value."""
        key = self._stt_key(side)
        return notional * self._stt_rates.get(key, 0.0)

    def _stt_key(self, side: str) -> str:
        sell = side.upper() == "SELL"
        if self.product in ("futures", "options"):
            return "fno_sell" if sell else "fno_buy"
        if self.delivery:
            return "equity_delivery_sell" if sell else "equity_delivery_buy"
        # Intraday equity: STT is sell-side only — the buy key is absent from
        # the STT dict so it resolves to 0.0 (never charge delivery STT on an
        # intraday buy).
        return "equity_intraday_sell" if sell else "equity_intraday_buy"

    def exchange_charge(self, notional: float) -> float:
        pct = self._exchange_rates.get(self.product, 0.0)
        return notional * pct

    def sebi(self, notional: float) -> float:
        return notional * self.sebi_fee

    def stamp(self, notional: float, side: str) -> float:
        """Stamp duty (buy side only in practice)."""
        if side.upper() == "SELL":
            return 0.0
        key = "fno" if self.product in ("futures", "options") else \
            ("equity_delivery_buy" if self.delivery else "equity_intraday")
        return notional * self._stamp_rates.get(key, 0.0)

    def gst(self, notional: float) -> float:
        base = self.brokerage + self.exchange_charge(notional) + self.sebi(notional)
        return base * self.gst_rate

    def total_cost(self, notional: float, side: str) -> float:
        """Full statutory charge for one leg (order side)."""
        return (
            self.stt(notional, side)
            + self.exchange_charge(notional)
            + self.sebi(notional)
            + self.stamp(notional, side)
            + self.gst(notional)
        )
