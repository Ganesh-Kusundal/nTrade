"""Guardrail for REF-1: OptionType is the single source of truth.

The architectural audit (SMELL-9) found ``"CE"``/``"PE"`` hardcoded across 14+
sites in 4 files. They must all reference ``domain.constants.OptionType``.
This test fails if any option-type comparison or validation uses a raw string
literal instead of the enum member, so the vocabulary can't silently drift back.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ntrade.domain.constants import OptionType

ROOT = Path(__file__).resolve().parents[1] / "ntrade"

# Files that are legitimately allowed to mention the literal "CE"/"PE" only as
# OptionType enum access. We scan for the *comparison/validation* idioms that
# previously hardcoded the strings.
RAW_OPTION_COMPARE = re.compile(
    r'option_type\s*==\s*["\']CE["\']|'
    r'option_type\s*==\s*["\']PE["\']|'
    r'option_type\s*!=\s*["\']CE["\']|'
    r'option_type\s*!=\s*["\']PE["\']|'
    r'option_type\s+not in\s*\(["\']CE["\']|'
    r'option_type\s+in\s*\(["\']CE["\']'
)

# Where raw CE/PE literals are still acceptable (e.g. Dhan wire-format labels
# that must read "CALL"/"PUT", or symbol string assembly) — not the audit
# target. We only flag option-type *comparisons*, which are never wire-format.

OPTION_FILES = [
    ROOT / "domain" / "instruments" / "derivatives.py",
    ROOT / "domain" / "instruments" / "chain.py",
    ROOT / "domain" / "instruments" / "expiry.py",
    ROOT / "domain" / "analytics" / "greeks.py",
    ROOT / "brokers" / "dhan_mapper.py",
    ROOT / "brokers" / "paper.py",
]


def test_option_type_is_strenum() -> None:
    """StrEnum keeps backward compatibility: legacy string comparisons still work."""
    assert OptionType.CE == "CE"
    assert OptionType.PE == "PE"
    assert OptionType("CE") is OptionType.CE
    assert OptionType("PE") is OptionType.PE


@pytest.mark.parametrize("path", OPTION_FILES, ids=lambda p: p.name)
def test_no_raw_option_type_comparison(path: Path) -> None:
    """No option-type comparison/validation may use a raw 'CE'/'PE' literal."""
    text = path.read_text()
    matches = RAW_OPTION_COMPARE.findall(text)
    assert matches == [], (
        f"{path.relative_to(ROOT.parent)} still compares option_type against a "
        f"raw literal (found {matches!r}); use OptionType.CE / OptionType.PE."
    )
