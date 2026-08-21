"""OptionChain — a first-class composite object containing Option instruments."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Iterator

import pandas as pd

from ntrade.domain.analytics.surface import GreeksTable, IVSurface
from ntrade.domain.instruments.expiry import Expiry, OptionPair

if TYPE_CHECKING:
    from ntrade.domain.instruments.derivatives import Future, Option
    from ntrade.domain.instruments.protocols import InstrumentProtocol


class OptionChain:
    """Composition of Option instruments, mirroring a real market chain.

    Example:
        chain = nifty.option_chain()
        chain.atm.greeks.delta
        chain.calls  /  chain.puts  /  chain.expiries
        chain.max_pain()  /  chain.pcr()
    """

    def __init__(
        self,
        underlying: "InstrumentProtocol",
        options: list[Option] | None = None,
        *,
        expiry: date | None = None,
        atm_strike: float | None = None,
        chain_df: pd.DataFrame | None = None,
    ):
        self.underlying = underlying
        self.target_expiry = expiry
        self.atm_strike = atm_strike
        self.chain_df = chain_df
        self._options: list[Option] = options or []
        # NOTE: brokers that fall back to another expiry (e.g. Dhan returning
        # None for the requested week) set this to the index actually used.
        # `target_expiry` may then be a placeholder (date.today()) rather than
        # the true contract expiry, so check this field before relying on it.
        self.expiry_index_used: int | None = None
        # Real contract expiries resolved from the broker (list[date]).
        self.expiry_list: list[date] = []
        # Strike index for O(1) lookups
        self._strike_map: dict[float, dict[str, Option]] = {}
        for o in self._options:
            self._strike_map.setdefault(o.strike, {})[o.option_type] = o

    # ------------------------------------------------------------------ build
    @classmethod
    def fetch(cls, underlying: "InstrumentProtocol", expiry: int = 0, num_strikes: int = 10, **kwargs) -> "OptionChain":
        """Fetch a live chain through the underlying's broker adapter."""
        broker = underlying.broker_adapter
        if broker is None:
            raise RuntimeError(f"{underlying} has no broker adapter to fetch an option chain")
        return broker.get_option_chain(underlying, expiry=expiry, num_strikes=num_strikes, **kwargs)

    # ------------------------------------------------------------------ views
    @property
    def calls(self) -> list[Option]:
        return [o for o in self._options if o.option_type == "CE"]

    @property
    def puts(self) -> list[Option]:
        return [o for o in self._options if o.option_type == "PE"]

    def expiries(self) -> list[Expiry]:
        """Return list of Expiry objects, one per expiry date."""
        dates = sorted({o.expiry for o in self._options})
        spot = self.underlying._quote.ltp or self.atm_strike or 0
        result = []
        for d in dates:
            opts = [o for o in self._options if o.expiry == d]
            result.append(Expiry(d, opts, spot))
        return result

    def expiry(self, offset: int = 0) -> Expiry:
        """Return Expiry at offset (0=nearest, 1=next, etc.)."""
        exps = self.expiries()
        if not exps:
            raise ValueError("No expiries available")
        idx = max(0, min(offset, len(exps) - 1))
        return exps[idx]

    def pairs(self) -> list[OptionPair]:
        """Return all OptionPairs across all expiries."""
        result = []
        for exp in self.expiries():
            result.extend(exp.pairs())
        return result

    @property
    def nearest_expiry(self) -> date | None:
        exps = self.expiries()
        return exps[0].date if exps else self.target_expiry
    @property
    def strikes(self) -> list[float]:
        return sorted(self._strike_map.keys())

    def at_strike(self, strike: float, option_type: str | None = None) -> Option | None:
        """O(1) strike lookup via pre-built index."""
        bucket = self._strike_map.get(strike)
        if bucket is None:
            return None
        if option_type:
            return bucket.get(option_type)
        return bucket.get("CE") or bucket.get("PE")

    @property
    def atm(self) -> Option | None:
        if self.atm_strike is not None:
            return self.at_strike(self.atm_strike, "CE") or self.at_strike(self.atm_strike)
        if not self._options:
            return None
        # Nearest strike to underlying LTP.
        spot = self.underlying._quote.ltp or self.atm_strike
        return min(self.calls or self._options, key=lambda o: abs(o.strike - (spot or 0)))

    @property
    def itm(self) -> list[Option]:
        return [o for o in self._options if o.moneyness(self.underlying._quote.ltp or self.atm_strike) == "ITM"]

    @property
    def otm(self) -> list[Option]:
        return [o for o in self._options if o.moneyness(self.underlying._quote.ltp or self.atm_strike) == "OTM"]

    # ------------------------------------------------------------------ analytics
    def pcr(self) -> float:
        """Put-Call Ratio based on open interest."""
        ce_oi = sum(o._quote.oi for o in self.calls)
        pe_oi = sum(o._quote.oi for o in self.puts)
        if ce_oi == 0:
            return 0.0
        return round(pe_oi / ce_oi, 4)

    def max_pain(self) -> float:
        """Strike where option buyers lose the most (max total payout to sellers)."""
        best_strike, min_pain = 0.0, float("inf")
        for strike in self.strikes:
            pain = 0.0
            for o in self._options:
                if o.option_type == "CE":
                    pain += max(strike - o.strike, 0.0) * max(o._quote.oi, 0)
                else:
                    pain += max(o.strike - strike, 0.0) * max(o._quote.oi, 0)
            if pain < min_pain:
                min_pain, best_strike = pain, strike
        return best_strike

    def iv_surface(self) -> IVSurface:
        rows = [{"strike": o.strike, "expiry": o.expiry, "type": o.option_type, "iv": o.iv} for o in self._options]
        df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["strike", "expiry", "type", "iv"])
        return IVSurface(df)

    def greeks_table(self) -> GreeksTable:
        rows = []
        for o in self._options:
            g = o.greeks
            rows.append({
                "strike": o.strike, "type": o.option_type,
                "delta": g.delta, "gamma": g.gamma,
                "theta": g.theta, "vega": g.vega,
                "iv": o.iv,
            })
        df = pd.DataFrame(rows) if rows else pd.DataFrame()
        return GreeksTable(df)

    def greeks(self) -> GreeksTable:
        """Alias matching the mission API (chain.greeks())."""
        return self.greeks_table()

    # ------------------------------------------------------------------ lifecycle
    def subscribe(self) -> "OptionChain":
        for o in self._options:
            o._stream.subscribe()
        return self

    def refresh(self) -> "OptionChain":
        return OptionChain.fetch(self.underlying)

    # ------------------------------------------------------------------ container
    def __len__(self) -> int:
        return len(self._options)

    def __iter__(self) -> Iterator[Option]:
        return iter(self._options)

    def __getitem__(self, strike: float) -> Option:
        opt = self.at_strike(strike)
        if opt is None:
            raise KeyError(f"No option at strike {strike}")
        return opt

    def __repr__(self) -> str:
        return f"OptionChain({self.underlying.symbol}, options={len(self._options)}, atm={self.atm_strike})"
