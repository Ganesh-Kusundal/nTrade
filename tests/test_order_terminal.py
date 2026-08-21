from ntrade.domain.orders.order import OrderStatus


def test_terminal_contains_all():
    assert OrderStatus.TERMINAL == frozenset({"COMPLETED", "REJECTED", "CANCELLED"})


def test_terminal_members_are_enum_values():
    assert all(isinstance(s, str) for s in OrderStatus.TERMINAL)
    assert OrderStatus.COMPLETED.value in OrderStatus.TERMINAL
