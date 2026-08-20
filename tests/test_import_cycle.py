def test_no_import_cycle():
    import pathlib
    base = pathlib.Path("ntrade/domain/instruments/base.py").read_text()
    assert "from ntrade.domain.instruments.chain" not in base
    import ntrade.domain.instruments.base
    import ntrade.domain.instruments.capabilities
    import ntrade.domain.instruments.chain
