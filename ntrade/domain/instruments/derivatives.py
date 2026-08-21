"""Derivative instruments: Future, Option, SyntheticInstrument."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import pandas as pd

from ntrade.domain.constants import DEFAULT_RISK_FREE_RATE
from ntrade.domain.instruments.base import Instrument

if TYPE_CHECKING:
    from ntrade.domain.analytics.greeks import Greeks


class Future(Instrument):
    KIND = "future"
    DEFAULT_EXCHANGE = "NFO"

    def __init__(
        self,
        symbol: str,
        exchange: str | None = None,
        *,
        underlying: str | None = None,
        expiry: date | None = None,
        **kwargs: Any,
    ):
        super().__init__(symbol, exchange or self.DEFAULT_EXCHANGE, **kwargs)
        self.underlying_symbol = underlying or symbol
        self.expiry = expiry
        self._underlying: Instrument | None = None
        self.front_month: "Future | None" = None
        self.next_month: "Future | None" = None

    def set_underlying(self, instrument: Instrument) -> "Future":
        self._underlying = instrument
        return self

    def set_front_month(self, fut: "Future") -> "Future":
        """Link this contract's front-month neighbour."""
        self.front_month = fut
        return self

    def set_next_month(self, fut: "Future") -> "Future":
        """Link this contract's next-month neighbour."""
        self.next_month = fut
        return self

    @property
    def underlying(self) -> Instrument | None:
        return self._underlying

    def basis(self) -> float:
        """Future price minus spot price."""
        if self._underlying is None or not self._underlying._quote.ltp:
            return 0.0
        return round(self._quote.ltp - self._underlying._quote.ltp, 4)

    def cost_of_carry(self, risk_free: float = DEFAULT_RISK_FREE_RATE) -> float:
        """Annualised cost of carry implied by the basis (approx. risk-free)."""
        if self._underlying is None or not self._underlying._quote.ltp or not self.expiry:
            return 0.0
        days = (self.expiry - date.today()).days
        if days <= 0:
            return 0.0
        spot = self._underlying._quote.ltp
        return round(((self._quote.ltp / spot - 1) * 365.0 / days) * 100, 4)

    def rollover(self, new_expiry: date, broker=None) -> "Future":
        """Roll the position to a new expiry, returning the new Future."""
        new_symbol = f"{self.underlying_symbol} {new_expiry:%d%b%y}"
        rolled = Future(new_symbol, self.exchange, underlying=self.underlying_symbol, expiry=new_expiry, broker=broker)
        if self._underlying is not None:
            rolled.set_underlying(self._underlying)
        return rolled

    def continuous(self, from_expiry: date | None = None, to_expiry: date | None = None) -> pd.DataFrame:
        """Back-adjust this future's price series into a continuous contract.

        Simple proportional back-adjustment: aligns the series to the first
        available close so seams between rollovers are removed.
        """
        df = self._history.df
        if df is None or df.empty or "close" not in df:
            return pd.DataFrame()
        if from_expiry is not None:
            df = df[df["timestamp"] >= pd.Timestamp(from_expiry)]
        if to_expiry is not None:
            df = df[df["timestamp"] <= pd.Timestamp(to_expiry)]
        out = df.copy()
        base = out["close"].iloc[0]
        for col in ("open", "high", "low", "close"):
            if col in out.columns:
                out[col] = out[col] / base * 100.0  # normalise to 100 at series start
        return out.reset_index(drop=True)

    def roll_yield(self) -> float:
        """Annualised roll yield (%). Positive = backwardation (near > next).

        Computed from the linked next-month contract: (self / next - 1) * 100.
        Returns 0.0 when no next-month contract is linked or prices are missing.
        """
        if self.next_month is None or not self.next_month._quote.ltp or not self._quote.ltp:
            return 0.0
        return round((self._quote.ltp / self.next_month._quote.ltp - 1) * 100, 4)


class Option(Instrument):
    KIND = "option"
    DEFAULT_EXCHANGE = "NFO"

    def __init__(
        self,
        symbol: str,
        exchange: str | None = None,
        *,
        strike: float,
        expiry: date,
        option_type: str,  # "CE" | "PE"
        underlying_symbol: str,
        exercise_style: str = "EUROPEAN",
        settlement: str = "CASH",
        **kwargs: Any,
    ):
        super().__init__(symbol, exchange or self.DEFAULT_EXCHANGE, **kwargs)
        self.strike = strike
        self.expiry = expiry
        self.option_type = option_type.upper()
        if self.option_type not in ("CE", "PE"):
            raise ValueError(f"option_type must be 'CE' or 'PE', got {option_type!r}")
        self.underlying_symbol = underlying_symbol
        self.exercise_style = exercise_style.upper()  # "EUROPEAN" | "AMERICAN"
        self.settlement = settlement.upper()          # "CASH" | "PHYSICAL"
        self._underlying: Instrument | None = None
        self._greeks: "Greeks | None" = None
        # ``None`` means "no IV computed yet" — distinct from a real 0.0 (L5).
        self.iv: float | None = None

    def set_underlying(self, instrument: Instrument) -> "Option":
        self._underlying = instrument
        return self

    @property
    def underlying(self) -> Instrument | None:
        return self._underlying

    # ------------------------------------------------------------------ analytics
    @property
    def greeks(self) -> "Greeks":
        """Always a Greeks object (zero-filled if unset) so chain.atm.greeks.delta never breaks."""
        if self._greeks is None:
            from ntrade.domain.analytics.greeks import Greeks
            return Greeks()
        return self._greeks

    def set_greeks(self, greeks: "Greeks") -> "Option":
        self._greeks = greeks
        if greeks.iv is not None:
            self.iv = greeks.iv
        return self

    @property
    def delta(self) -> float:
        return self._greeks.delta if self._greeks else float("nan")

    @property
    def gamma(self) -> float:
        return self._greeks.gamma if self._greeks else float("nan")

    @property
    def theta(self) -> float:
        return self._greeks.theta if self._greeks else float("nan")

    @property
    def vega(self) -> float:
        return self._greeks.vega if self._greeks else float("nan")

    @property
    def rho(self) -> float:
        return self._greeks.rho if self._greeks else float("nan")

    def intrinsic_value(self, spot: float | None = None) -> float:
        s = spot if spot is not None else (self._underlying._quote.ltp if self._underlying else self._quote.ltp)
        if self.option_type == "CE":
            return max(s - self.strike, 0.0)
        return max(self.strike - s, 0.0)

    def extrinsic_value(self, spot: float | None = None) -> float:
        return round(max(self._quote.ltp - self.intrinsic_value(spot), 0.0), 4)

    def moneyness(self, spot: float | None = None) -> str:
        s = spot if spot is not None else (self._underlying._quote.ltp if self._underlying else self._quote.ltp)
        if s == 0:
            return "unknown"
        if self.option_type == "CE":
            return "ITM" if s > self.strike else ("ATM" if abs(s - self.strike) / s < 0.005 else "OTM")
        return "ITM" if s < self.strike else ("ATM" if abs(s - self.strike) / s < 0.005 else "OTM")

    def black_scholes(self, spot: float, risk_free: float = DEFAULT_RISK_FREE_RATE, sigma: float | None = None,
                      *, now: "date | datetime | None" = None) -> float:
        """Theoretical price using Black-Scholes.

        ``now`` (M-3): replay sessions inject the session date so pricing is
        deterministic; ``None`` keeps the wall clock for live/standalone use.
        """
        from ntrade.domain.analytics.greeks import BlackScholes
        sigma = sigma if sigma is not None else (self.iv or 0.15)
        t = self._years_to_expiry(now)
        return BlackScholes.price(spot, self.strike, t, risk_free, sigma, self.option_type)

    def implied_volatility(self, market_price: float, spot: float, risk_free: float = DEFAULT_RISK_FREE_RATE,
                           *, now: "date | datetime | None" = None) -> float:
        from ntrade.domain.analytics.greeks import BlackScholes
        return BlackScholes.implied_volatility(market_price, spot, self.strike,
                                               self._years_to_expiry(now), risk_free, self.option_type)

    def payoff(self, spot: float, premium: float | None = None) -> float:
        p = premium if premium is not None else self._quote.ltp
        intrinsic = self.intrinsic_value(spot)
        return round((intrinsic - p) * (self.lot_size or 1), 2)

    def pnl(self, buy_price: float | None = None) -> float:
        buy = buy_price if buy_price is not None else self._quote.ltp
        return round((self._quote.ltp - buy) * (self.lot_size or 1), 2)

    def _years_to_expiry(self, now: "date | datetime | None" = None) -> float:
        # M-3: ``now`` is injectable so replay analytics follow session time;
        # None preserves the wall-clock default for live/standalone callers.
        if now is None:
            now = datetime.now()
        if isinstance(now, datetime):
            now = now.date()
        delta = (self.expiry - now).days
        return max(delta / 365.0, 1 / 365.0)


class SyntheticInstrument(Instrument):
    """A composite instrument made of other instruments (e.g. a straddle)."""

    KIND = "synthetic"
    DEFAULT_EXCHANGE = "NFO"

    def __init__(self, symbol: str, legs: list[Instrument], exchange: str | None = None, **kwargs: Any):
        super().__init__(symbol, exchange, **kwargs)
        self.legs = legs

    @property
    def ltp(self) -> float:
        return round(sum(leg.ltp for leg in self.legs), 4)

    def payoff(self, spot: float) -> float:
        return round(sum(leg.payoff(spot) for leg in self.legs), 2)
