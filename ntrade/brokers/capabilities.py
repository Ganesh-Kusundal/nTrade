"""Capability pattern — broker-specific extensions without polluting the base API.

This module is a backward-compatibility shim: the capability machinery
(``Capability``, ``capability()``, ``registered_capabilities()`` and
``BrokerExtensionFacade``) now lives in the domain layer
(``ntrade.domain.ports``) — it is pure plugin infrastructure with no broker
dependency. Broker modules register their capabilities here via the
``@capability`` decorator (imported from this module or from the domain port).
"""

from ntrade.domain.ports import BrokerExtensionFacade, Capability, capability, registered_capabilities

__all__ = ["Capability", "capability", "registered_capabilities", "BrokerExtensionFacade"]
