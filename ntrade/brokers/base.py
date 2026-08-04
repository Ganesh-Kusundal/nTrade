"""BrokerAdapter — the hidden transport boundary between domain objects and brokers.

This module is a backward-compatibility shim: the ``BrokerAdapter`` ABC now
lives in the domain layer (``ntrade.domain.ports``) so the dependency arrow
points outward — brokers implement the port, the domain never imports brokers.
Keep importing ``BrokerAdapter`` from here or from ``ntrade.brokers``; both
resolve to the same class.
"""

from ntrade.domain.ports import BrokerAdapter

__all__ = ["BrokerAdapter"]
