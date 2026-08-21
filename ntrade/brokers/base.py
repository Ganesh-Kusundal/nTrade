"""BrokerAdapter — deprecated shim (use ``ntrade.domain.ports``)."""

import warnings

warnings.warn(
    "ntrade.brokers.base is deprecated; import BrokerAdapter from ntrade.domain.ports instead",
    DeprecationWarning,
    stacklevel=2,
)

from ntrade.domain.ports import BrokerAdapter  # noqa: E402, F401

__all__ = ["BrokerAdapter"]
