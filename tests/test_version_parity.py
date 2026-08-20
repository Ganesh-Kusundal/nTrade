def test_version_parity():
    import importlib.metadata
    assert importlib.metadata.version("ntrade") == __import__("ntrade").__version__


def test_architecture_tree_fresh():
    text = open("ARCHITECTURE.md").read()
    assert "kernel/trading_session.py" in text
    assert "dhan_auth_provider.py" in text
    assert "2026-08-20" in text


def test_pyproject_version_and_deps():
    text = open("pyproject.toml").read()
    assert 'version = "0.2.0"' in text
    assert 'requires-python = ">=3.10"' in text
    assert "httpx>=0.27" in text
    assert "httpx2" not in text
