"""Capability pattern — deprecated shim (use ``ntrade.domain.ports``)."""

import warnings

warnings.warn(
    "ntrade.brokers.capabilities is deprecated; import from ntrade.domain.ports instead",
    DeprecationWarning,
    stacklevel=2,
)

from ntrade.domain.ports import BrokerExtensionFacade, Capability, capability, registered_capabilities  # noqa: E402, F401

__all__ = ["Capability", "capability", "registered_capabilities", "BrokerExtensionFacade"]
