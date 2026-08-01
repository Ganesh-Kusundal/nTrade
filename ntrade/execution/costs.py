"""Cost models — slippage, commission and Indian-market statutory charges.

Lives in ``execution/`` (not ``backtest/``) because simulated execution is
shared by live paper trading, replay and backtest — zero parity demands the
same cost pipeline everywhere.

Statutory costs (H6): STT, exchange transaction charges, SEBI fee, GST on
brokerage and stamp duty are what make Indian backtest PnL diverge from live.
They are modeled as an additive ``IndianStatutoryCosts`` component configured
with a brokerage amount; ``total_cost(notional, side)`` returns the full
charge so callers can report realised PnL accurately, and
``for_instrument()`` picks the product schedule (equity/futures/options) from
the instrument class so F&O backtests are not silently charged equity rates.
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

    def gst(self, notional: float, *, brokerage: float | None = None) -> float:
        """GST (18%) on brokerage + exchange charge + SEBI fee.

        ``brokerage`` overrides the model's configured amount per call — the
        execution layer passes the actual per-fill commission so GST is charged
        on the commission the user configured, exactly like a live payout.
        """
        brk = self.brokerage if brokerage is None else brokerage
        base = brk + self.exchange_charge(notional) + self.sebi(notional)
        return base * self.gst_rate

    def total_cost(
        self, notional: float, side: str, *, brokerage: float | None = None,
    ) -> float:
        """Full statutory charge for one leg (order side)."""
        return (
            self.stt(notional, side)
            + self.exchange_charge(notional)
            + self.sebi(notional)
            + self.stamp(notional, side)
            + self.gst(notional, brokerage=brokerage)
        )

    def for_instrument(
        self, instrument, *, delivery: bool | None = None,
    ) -> "IndianStatutoryCosts":
        """Product/delivery-adjusted model for an instrument's class.

        Futures and Options use the F&O STT/stamp/exchange schedule; everything
        else keeps the configured product (default equity-intraday). Custom
        rates (``stt``/``exchange_charge``/``stamp_duty``/…) are preserved — only
        the schedule keys change. ``delivery`` overrides the delivery flag
        (overnight equity holds switch to the delivery schedule). Returns
        ``self`` when no adjustment applies.
        """
        from ntrade.domain.instruments.derivatives import Future, Option

        product, d = self.product, self.delivery
        if isinstance(instrument, Option):
            product, d = "options", False
        elif isinstance(instrument, Future):
            product, d = "futures", False
        if delivery is not None:
            d = bool(delivery)
        if product == self.product and d == self.delivery:
            return self
        return IndianStatutoryCosts(
            product=product, delivery=d, brokerage=self.brokerage,
            stt=self._stt_rates, exchange_charge=self._exchange_rates,
            sebi_fee=self.sebi_fee, gst_rate=self.gst_rate,
            stamp_duty=self._stamp_rates,
        )


class FuturesCarryCosts:
    """Futures holding-period costs: daily carry (roll yield) + expiry roll.

    A backtest that holds a futures contract across bars and into expiry pays
    the carry drag live (contango) and pays spread/slippage when rolling to the
    next contract. Modelling these makes a futures backtest's PnL converge on
    live roll behavior:

      - ``daily_carry(notional, days)`` — the cost-of-carry drag on the
        contract value: ``notional * (risk_free - dividend_yield) * days/365``.
        A long position pays it (contango); a short position receives it.
      - ``roll_cost(notional)`` — one-off slippage charged when a held contract
        crosses its expiry and must be rolled to the next month.

    ``carry_window_days`` limits carry accrual to the run-up to expiry (the
    roll window); outside it, prices already embed the term structure and
    charging again would double-count.
    """

    def __init__(
        self,
        *,
        risk_free: float = 0.065,
        dividend_yield: float = 0.0,
        roll_pct: float = 0.0002,
        carry_window_days: int = 5,
    ):
        self.risk_free = risk_free
        self.dividend_yield = dividend_yield
        self.roll_pct = roll_pct
        self.carry_window_days = carry_window_days

    def daily_carry(self, notional: float, days: int) -> float:
        """Cost-of-carry drag on ``notional`` over ``days`` (contango)."""
        if days <= 0:
            return 0.0
        return notional * (self.risk_free - self.dividend_yield) * days / 365.0

    def roll_cost(self, notional: float) -> float:
        """One-off slippage paid when rolling a held contract past expiry."""
        return notional * self.roll_pct

    def within_window(self, expiry, now) -> bool:
        """True while ``now`` is inside the carry/roll window before expiry."""
        from datetime import date

        if expiry is None:
            return False
        days_to = (expiry - now).days if isinstance(now, date) else \
            (expiry - now.date()).days
        return 0 <= days_to <= self.carry_window_days


class _StatutoryDefault:
    """Sentinel meaning 'use IndianStatutoryCosts() defaults'.

    Distinct from ``None`` (zero-cost opt-out) so simulated execution can
    default to realistic Indian charges while still allowing an explicit
    zero-cost mode — backtests that ignore statutory charges do not converge
    on live PnL.
    """

    def __repr__(self) -> str:
        return "<default Indian statutory costs>"


STATUTORY_DEFAULT = _StatutoryDefault()


def resolve_statutory(model):
    """Resolve a ``statutory`` argument to a cost model or ``None``.

    ``STATUTORY_DEFAULT`` → a fresh ``IndianStatutoryCosts()``; ``None`` →
    zero-cost opt-out; anything else is used as-is (custom rates).
    """
    if model is STATUTORY_DEFAULT:
        return IndianStatutoryCosts()
    return model
