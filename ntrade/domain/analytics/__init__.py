"""Domain analytics — pure indicator / greeks math.

Explicit coupling note: chart overlay pipeline lives at
ntrade.analytics.overlay_pipeline and is registered as indicator spec
'halftrend' in ntrade.domain.analytics.indicators (metadata-only contract).
"""

from __future__ import annotations

import typing

from ntrade.domain.analytics.greeks import BlackScholes, Greeks

if typing.TYPE_CHECKING:  # visible coupling for 'halftrend' — calc is ntrade.analytics.overlay_pipeline
    from ntrade.analytics import overlay_pipeline as _overlay_pipeline  # noqa: F401

__all__ = ["Greeks", "BlackScholes"]
