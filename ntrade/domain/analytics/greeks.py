"""Greeks and the Black-Scholes pricing engine (pure math, no external deps)."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Greeks:
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0
    iv: float = 0.0

    def as_dict(self) -> dict:
        return {
            "delta": self.delta, "gamma": self.gamma, "theta": self.theta,
            "vega": self.vega, "rho": self.rho, "iv": self.iv,
        }


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


class BlackScholes:
    """Static pricing engine: price, greeks, implied volatility."""

    @staticmethod
    def price(
        spot: float, strike: float, years: float, risk_free: float,
        sigma: float, option_type: str = "CE", dividend: float = 0.0,
    ) -> float:
        if years <= 0:
            intrinsic = max(spot - strike, 0.0) if option_type == "CE" else max(strike - spot, 0.0)
            return round(intrinsic, 4)
        if sigma <= 0:
            raise ValueError("sigma must be positive")
        sqrt_t = math.sqrt(years)
        d1 = (math.log(spot / strike) + (risk_free - dividend + 0.5 * sigma ** 2) * years) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
        if option_type == "CE":
            value = spot * math.exp(-dividend * years) * _norm_cdf(d1) - strike * math.exp(-risk_free * years) * _norm_cdf(d2)
        else:
            value = strike * math.exp(-risk_free * years) * _norm_cdf(-d2) - spot * math.exp(-dividend * years) * _norm_cdf(-d1)
        return round(value, 4)

    @staticmethod
    def greeks(
        spot: float, strike: float, years: float, risk_free: float,
        sigma: float, option_type: str = "CE", dividend: float = 0.0,
    ) -> Greeks:
        sqrt_t = math.sqrt(years)
        d1 = (math.log(spot / strike) + (risk_free - dividend + 0.5 * sigma ** 2) * years) / (sigma * sqrt_t)
        d2 = d1 - sigma * sqrt_t
        pdf_d1 = _norm_pdf(d1)
        delta = math.exp(-dividend * years) * (_norm_cdf(d1) if option_type == "CE" else _norm_cdf(d1) - 1)
        gamma = math.exp(-dividend * years) * pdf_d1 / (spot * sigma * sqrt_t)
        vega = spot * math.exp(-dividend * years) * pdf_d1 * sqrt_t / 100.0
        theta = (
            -spot * math.exp(-dividend * years) * pdf_d1 * sigma / (2 * sqrt_t)
            - risk_free * strike * math.exp(-risk_free * years) * (_norm_cdf(d2) if option_type == "CE" else _norm_cdf(-d2))
            + (dividend * spot * math.exp(-dividend * years) * (_norm_cdf(d1) if option_type == "CE" else _norm_cdf(-d1)))
        ) / 365.0
        rho = (
            strike * years * math.exp(-risk_free * years)
            * (_norm_cdf(d2) if option_type == "CE" else _norm_cdf(-d2)) / 100.0
        )
        return Greeks(
            delta=round(delta, 6), gamma=round(gamma, 6),
            theta=round(theta, 6), vega=round(vega, 6), rho=round(rho, 6), iv=round(sigma, 4),
        )

    @staticmethod
    def implied_volatility(
        market_price: float, spot: float, strike: float, years: float,
        risk_free: float, option_type: str = "CE", dividend: float = 0.0,
        tol: float = 1e-6, max_iter: int = 100,
    ) -> float:
        """Bisection IV solver. Returns 0.0 if no solution within bounds."""
        if years <= 0 or market_price <= 0:
            return 0.0
        intrinsic = max(spot - strike, 0.0) if option_type == "CE" else max(strike - spot, 0.0)
        if market_price < intrinsic:
            return 0.0
        lo, hi = 0.0001, 5.0
        if BlackScholes.price(spot, strike, years, risk_free, hi, option_type, dividend) < market_price:
            return 0.0
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
