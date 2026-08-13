"""Registry integration tests for the strategy + indicator registries.

These tests import the real ntrade package (which fires all registration
side-effects) and assert the registry contract holds — no mocks, no stubs.
"""


def test_strategy_registry_resolves_known_ids():
    """strategy.get("valentini") returns the right spec + class wiring."""
    import ntrade
    from ntrade.registry import strategy
    from ntrade.engines.strategies import _strategy_classes
    from ntrade.engines.strategies import ValentiniScalper, EmaCrossStrategy
    from ntrade.engines.morning_vah_val import MorningVAHVAL

    spec = strategy.get("valentini")
    assert spec.id == "valentini"
    assert spec.label == "Valentini Scalper"
    # params key set is a subset of the constructor kwargs (defaults)
    assert set(spec.params) <= set(ValentiniScalper.__init__.__code__.co_varnames)
    # The implementation lookup dict matches the spec id
    assert _strategy_classes["valentini"] is ValentiniScalper

    assert strategy.get("ema_cross").id == "ema_cross"
    assert _strategy_classes["ema_cross"] is EmaCrossStrategy

    assert strategy.get("morning_vah_val").id == "morning_vah_val"
    assert _strategy_classes["morning_vah_val"] is MorningVAHVAL


def test_indicator_registry_non_empty_and_rsi_has_period():
    from ntrade.registry import indicator, IndicatorSpec

    assert len(indicator.registry) > 0
    rsi_spec = indicator.get("rsi")
    assert isinstance(rsi_spec, IndicatorSpec)
    assert "rsi_period" in rsi_spec.params
    assert rsi_spec.params["rsi_period"] == 14


def test_registries_importable_and_nonempty():
    """Contract: import ntrade; ntrader.strategy/indicator are non-empty."""
    import ntrade
    assert len(ntrade.indicator.registry) > 0
    assert len(ntrade.strategy.registry) > 0
    assert "rsi" in ntrade.indicator.registry
    assert "valentini" in ntrade.strategy.registry


def test_paper_trader_resolves_strategy_via_registry():
    """paper_trader no longer imports MorningVAHVAL directly by name."""
    import ast
    with open("api/paper_trader.py") as f:
        tree = ast.parse(f.read())
    # No ``from ntrade.engines.morning_vah_val import MorningVAHVAL`` at module
    # level — the strategy is resolved through the registry inside _build_session.
    top_level_imports = [
        n for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for n in (node.names or [])
    ]
    assert not any("MorningVAHVAL" in n.name for n in top_level_imports)
