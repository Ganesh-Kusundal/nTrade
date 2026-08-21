"""Expiry and OptionPair — structured navigation over an OptionChain.

An ``Expiry`` groups all options sharing the same expiry date, providing
ATM/OTM/ITM selection and strike-pair views.  An ``OptionPair`` bundles the
call and put at a single strike.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from ntrade.domain.constants import OptionType

if TYPE_CHECKING:
    from ntrade.domain.instruments.derivatives import Option


@dataclass(frozen=True)
class OptionPair:
    """Call + put at a single strike."""

    strike: float
    call: "Option | None" = None
    put: "Option | None" = None

    # ---- derived analytics ---------------------------------------------------
    @property
    def straddle_premium(self) -> float:
        """Total premium of the ATM straddle (call + put LTP)."""
        c = self.call._quote.ltp if self.call else 0.0
        p = self.put._quote.ltp if self.put else 0.0
        return round(c + p, 4)

    @property
    def pcr(self) -> float:
        """Put-call ratio at this strike (OI-based)."""
        ce_oi = self.call._quote.oi if self.call else 0
        pe_oi = self.put._quote.oi if self.put else 0
        if ce_oi == 0:
            return 0.0
        return round(pe_oi / ce_oi, 4)

    def synthetic_long_price(self) -> float:
        """Call premium minus put premium (synthetic forward)."""
        c = self.call._quote.ltp if self.call else 0.0
        p = self.put._quote.ltp if self.put else 0.0
        return round(c - p, 4)

    def synthetic_short_price(self) -> float:
        """Put premium minus call premium."""
        return round(-self.synthetic_long_price(), 4)


class Expiry:
    """All options sharing a single expiry date, with navigation helpers."""

    def __init__(self, expiry_date: date, options: list["Option"], atm_strike: float):
        self.date = expiry_date
        self._options = options
        self._atm_strike = atm_strike
        # Pre-build strike-sorted view
        self._strikes = sorted({o.strike for o in options})
        # Build pair lookup
        self._pair_map: dict[float, OptionPair] = {}
        calls = {o.strike: o for o in options if o.option_type == OptionType.CE}
        puts = {o.strike: o for o in options if o.option_type == OptionType.PE}
        for s in self._strikes:
            self._pair_map[s] = OptionPair(strike=s, call=calls.get(s), put=puts.get(s))

    # ---- strike selection ----------------------------------------------------
    def atm(self, offset: int = 0) -> OptionPair:
        """ATM pair, shifted by *offset* strikes (0 = nearest to spot)."""
        idx = self._atm_index + offset
        idx = max(0, min(idx, len(self._strikes) - 1))
        return self._pair_map[self._strikes[idx]]

    def pair_at(self, strike: float) -> OptionPair:
        """Pair at an exact strike; raises ``KeyError`` if absent."""
        if strike not in self._pair_map:
            raise KeyError(f"No option pair at strike {strike}")
        return self._pair_map[strike]

    def otm(self, n: int) -> list["Option"]:
        """Top *n* OTM options (interleaved: calls above ATM, puts below ATM)."""
        atm_idx = self._atm_index
        # OTM calls (strikes above ATM, nearest first)
        otm_calls: list[Option] = []
        for s in self._strikes[atm_idx + 1:]:
            for o in self._options:
                if o.strike == s and o.option_type == OptionType.CE:
                    otm_calls.append(o)
                    break
        # OTM puts (strikes below ATM, nearest first)
        otm_puts: list[Option] = []
        for s in reversed(self._strikes[:atm_idx]):
            for o in self._options:
                if o.strike == s and o.option_type == OptionType.PE:
                    otm_puts.append(o)
                    break
        # Interleave: call, put, call, put …
        result: list[Option] = []
        ci, pi = 0, 0
        while len(result) < n:
            added = False
            if ci < len(otm_calls):
                result.append(otm_calls[ci])
                ci += 1
                added = True
            if len(result) >= n:
                break
            if pi < len(otm_puts):
                result.append(otm_puts[pi])
                pi += 1
                added = True
            if not added:
                break
        return result

    def itm(self, n: int) -> list["Option"]:
        """Top *n* ITM options (interleaved: calls below ATM, puts above ATM)."""
        atm_idx = self._atm_index
        # ITM calls (strikes below ATM, nearest first)
        itm_calls: list[Option] = []
        for s in reversed(self._strikes[:atm_idx]):
            for o in self._options:
                if o.strike == s and o.option_type == OptionType.CE:
                    itm_calls.append(o)
                    break
        # ITM puts (strikes above ATM, nearest first)
        itm_puts: list[Option] = []
        for s in self._strikes[atm_idx + 1:]:
            for o in self._options:
                if o.strike == s and o.option_type == OptionType.PE:
                    itm_puts.append(o)
                    break
        # Interleave: call, put, call, put …
        result: list[Option] = []
        ci, pi = 0, 0
        while len(result) < n:
            added = False
            if ci < len(itm_calls):
                result.append(itm_calls[ci])
                ci += 1
                added = True
            if len(result) >= n:
                break
            if pi < len(itm_puts):
                result.append(itm_puts[pi])
                pi += 1
                added = True
            if not added:
                break
        return result

    # ---- views ---------------------------------------------------------------
    def pairs(self) -> list[OptionPair]:
        return [self._pair_map[s] for s in self._strikes]

    def calls(self) -> list["Option"]:
        return [o for o in self._options if o.option_type == OptionType.CE]

    def puts(self) -> list["Option"]:
        return [o for o in self._options if o.option_type == OptionType.PE]

    def strikes(self) -> list[float]:
        return list(self._strikes)

    # ---- internal ------------------------------------------------------------
    @property
    def _atm_index(self) -> int:
        """Index into ``_strikes`` closest to the ATM strike."""
        if not self._strikes:
            return 0
        return min(range(len(self._strikes)),
                   key=lambda i: abs(self._strikes[i] - self._atm_strike))

    def __repr__(self) -> str:
        return f"Expiry(date={self.date}, strikes={len(self._strikes)}, atm={self._atm_strike})"
