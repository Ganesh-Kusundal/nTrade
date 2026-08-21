"""Greeks and the Black-Scholes pricing engine (pure math, no external deps)."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ntrade.domain.constants import OptionType

#: Sentinel for "no value was computed". Distinct from a genuine 0.0 so a
#: not-computed IV/delta can never be mistaken for a real zero (L5).
NOT_COMPUTED: float | None = None


@dataclass(frozen=True)
class Greeks:
    delta: float | None = 0.0
    gamma: float | None = 0.0
    theta: float | None = 0.0
    vega: float | None = 0.0
    rho: float | None = 0.0
    iv: float | None = NOT_COMPUTED

    def as_dict(self) -> dict:
        return {
            "delta": self.delta, "gamma": self.gamma, "theta": self.theta,
            "vega": self.vega, "rho": self.rho, "iv": self.iv,
        }

    @property
    def computed(self) -> bool:
        """True when at least one greek was actually computed (not the
        zero-filled default), so callers can distinguish "0.0" from
        "not computed" (L5)."""
        return self.delta not in (None, 0.0) or self.gamma not in (None, 0.0) \
            or self.theta not in (None, 0.0) or self.vega not in (None, 0.0) \
            or self.rho not in (None, 0.0) or self.iv not in (None, 0.0)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


class BlackScholes:
    """Static pricing engine: price, greeks, implied volatility."""

    @staticmethod
    def price(
        spot: float, strike: float, years: float, risk_free: float,
        sigma: float, option_type: "OptionType | str" = OptionType.CE, dividend: float = 0.0,
    ) -> float:
        if years <= 0:
            intrinsic = max(spot - strike, 0.0) if option_type == OptionType.CE else max(strike - spot, 0.0)
            return round(intrinsic, 4)
        if sigma <= 0:
            raise ValueError("sigma must be positive")
        sqrt_t = math.sqrt(years)
        d1 = (math.log(spot / strike) + (risk_free - dividend + 0.5 * sigma ** 2) * years) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
        if option_type == OptionType.CE:
            value = spot * math.exp(-dividend * years) * _norm_cdf(d1) - strike * math.exp(-risk_free * years) * _norm_cdf(d2)
        else:
            value = strike * math.exp(-risk_free * years) * _norm_cdf(-d2) - spot * math.exp(-dividend * years) * _norm_cdf(-d1)
        return round(value, 4)

    @staticmethod
    def greeks(
        spot: float, strike: float, years: float, risk_free: float,
        sigma: float, option_type: "OptionType | str" = OptionType.CE, dividend: float = 0.0,
    ) -> Greeks:
        sqrt_t = math.sqrt(years)
        d1 = (math.log(spot / strike) + (risk_free - dividend + 0.5 * sigma ** 2) * years) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
        pdf_d1 = _norm_pdf(d1)
        delta = math.exp(-dividend * years) * (_norm_cdf(d1) if option_type == OptionType.CE else _norm_cdf(d1) - 1)
        gamma = math.exp(-dividend * years) * pdf_d1 / (spot * sigma * sqrt_t)
        vega = spot * math.exp(-dividend * years) * pdf_d1 * sqrt_t / 100.0
        theta = (
            -spot * math.exp(-dividend * years) * pdf_d1 * sigma / (2 * sqrt_t)
            - risk_free * strike * math.exp(-risk_free * years) * (_norm_cdf(d2) if option_type == OptionType.CE else _norm_cdf(-d2))
            + (dividend * spot * math.exp(-dividend * years) * (_norm_cdf(d1) if option_type == OptionType.CE else _norm_cdf(-d1)))
        ) / 365.0
        rho = (
            strike * years * math.exp(-risk_free * years)
            * (_norm_cdf(d2) if option_type == OptionType.CE else _norm_cdf(-d2)) / 100.0
        )
        return Greeks(
            delta=round(delta, 6), gamma=round(gamma, 6),
            theta=round(theta, 6), vega=round(vega, 6), rho=round(rho, 6), iv=round(sigma, 4),
        )

    @staticmethod
    def implied_volatility(
        market_price: float, spot: float, strike: float, years: float,
        risk_free: float, option_type: "OptionType | str" = OptionType.CE, dividend: float = 0.0,
        tol: float = 1e-6, max_iter: int = 100,
    ) -> float | None:
        """Bisection IV solver. Returns ``NOT_COMPUTED`` (None) when no
        solution exists within bounds — never a misleading 0.0 (L5)."""
        if years <= 0 or market_price <= 0:
            return NOT_COMPUTED
        intrinsic = max(spot - strike, 0.0) if option_type == OptionType.CE else max(strike - spot, 0.0)
        if market_price < intrinsic:
            return NOT_COMPUTED
        lo, hi = 0.0001, 5.0
        if BlackScholes.price(spot, strike, years, risk_free, hi, option_type, dividend) < market_price:
            return NOT_COMPUTED
        for _ in range(max_iter):
            mid = (lo + hi) / 2.0
            price = BlackScholes.price(spot, strike, years, risk_free, mid, option_type, dividend)
            if abs(price - market_price) < tol:
                return round(mid, 6)
            if price < market_price:
                lo = mid
            else:
                hi = mid
        return round((lo + hi) / 2.0, 6)
