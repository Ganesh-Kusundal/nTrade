# tests/test_single_sources.py
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]

def _py_texts(root: str):
    return [p.read_text() for p in (REPO / root).rglob("*.py") if ".venv" not in str(p)]

def test_no_zoneinfo_duplicate():
    hits = []
    for p in (REPO / "ntrade").rglob("*.py"):
        if ".venv" in str(p):
            continue
        txt = p.read_text()
        if 'ZoneInfo("Asia/Kolkata")' in txt and p.name != "market_hours.py":
            hits.append(str(p.relative_to(REPO)))
    assert not hits, f"ZoneInfo('Asia/Kolkata') duplicated outside market_hours.py: {hits}"

def test_no_local_interval_dict_outside_timeframes():
    offenders = []
    for root in ("ntrade", "api"):
        for p in (REPO / root).rglob("*.py"):
            if p.name in ("timeframes.py",):
                continue
            txt = p.read_text()
            # Ban local dict literals that look like interval tables.
            if re.search(r'_INTERVAL_(?:MINUTES|SECONDS)\s*=\s*\{', txt):
                # candle_engine keeps a legacy alias, marketdata keeps a derived alias — allow them
                if p.name in ("candle_engine.py", "marketdata.py"):
                    continue
                offenders.append(str(p.relative_to(REPO)))
    # In this repo the only offenders are the alias-fossils; any new one is a defect.
    assert not offenders, f"New _INTERVAL_* dict outside timeframes.py: {offenders}"

def test_no_local_terminal_status_set():
    hits = []
    pat = re.compile(r'\{"COMPLETED"\s*,\s*"REJECTED"\s*,\s*"CANCELLED"')
    for root in ("ntrade", "api"):
        for p in (REPO / root).rglob("*.py"):
            txt = p.read_text()
            if pat.search(txt):
                hits.append(str(p.relative_to(REPO)))
    assert not hits, f"Terminal-status literal duplicated (use OrderStatus.TERMINAL): {hits}"

def test_no_ghost_market_hours_docstring():
    offenders = [str(p.relative_to(REPO)) for p in (REPO / "api").rglob("*.py") if "api.market_hours" in p.read_text()]
    assert not offenders, f"Ghost api.market_hours docstring remains: {offenders}"
