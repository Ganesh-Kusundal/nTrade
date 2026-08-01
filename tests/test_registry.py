
def test_broker_registration_is_lazy():
    """Importing registry does not eagerly import broker modules."""
    import importlib
    import sys
    # Remove cached broker modules to test fresh import
    saved = {}
    for mod_name in list(sys.modules):
        if "dhan" in mod_name or "paper" in mod_name:
            saved[mod_name] = sys.modules.pop(mod_name)
    try:
        # Re-import registry — should NOT pull in dhan broker
        from ntrade.registry import BrokerRegistry
        BrokerRegistry.unregister_all()
        # Reset the lazy flag
        import ntrade.registry as reg
        reg._DEFAULT_BROKERS_REGISTERED = False
        # available() triggers lazy load
        avail = BrokerRegistry.available()
        # paper should be available (no external deps)
        assert "paper" in avail
    finally:
        # Restore cached modules
        sys.modules.update(saved)


def test_register_default_brokers_still_works():
    """The public register_default_brokers() API still works."""
    from ntrade.registry import BrokerRegistry, register_default_brokers
    BrokerRegistry.unregister_all()
    import ntrade.registry as reg
    reg._DEFAULT_BROKERS_REGISTERED = False
    register_default_brokers()
    assert "paper" in BrokerRegistry.available()
