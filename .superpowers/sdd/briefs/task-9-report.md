# Task 9 (T-017) Report — Arm scanner M6 throttle + dedupe `Scanner.top` ranking

## Commit
- **Hash:** `4c8c3e1d2ddbcb980026965af3fda847bad23d54`
- **Subject:** `T-017 arm M6 scanner throttle; dedupe Scanner.top ranking`

## `git show --stat HEAD`
```
 ntrade/domain/scanner.py   | 14 ++++----------
 ntrade/scanners/builtin.py |  3 +++
 tests/test_scanner.py      | 43 +++++++++++++++++++++++--------------------
 3 files changed, 30 insertions(+), 30 deletions(-)
```

## Test suite
Command: `./.venv/bin/python -m pytest -q`
**Result: 631 passed** (baseline 630 + 1 new throttle test; `test_top_ranks_by_score` removed and replaced with a facade test; net +1).

## `Scanner.top` caller audit
Confirmed `Scanner.top` had **exactly one caller** repo-wide: `tests/test_scanner.py:120` (`test_top_ranks_by_score`, which used a custom domain `Scanner`). No production callers in `ntrade/`. After the change a `grep -rn '\.top(' ntrade/ tests/` returns no matches. Removed the `Scanner.top` method (`scanner.py:64-70`) and updated the ABC docstring; `ScannerFacade._run` (`scanner.py:136-165`) remains the canonical rank (score desc) + rank 1..N path.

## Scanners armed with `rate_limit_seconds`
| Scanner | `rate_limit_seconds` |
|---|---|
| `MomentumScanner` | `30.0` |
| `VolumeSpikeScanner` | `30.0` |
| `BreakoutScanner` | `30.0` |

`GapScanner` and `ImbalanceScanner` left at base `0.0` (unchanged, out of scope, per resolved plan inconsistency).

## Tests added/changed (`tests/test_scanner.py`)
- **Added:** `test_builtin_scanners_arm_m6_throttle` (asserts Volume/Momentum/Breakout have `rate_limit_seconds > 0`; Gap/Imbalance stay `0.0`).
- **Replaced:** `test_top_ranks_by_score` → `test_facade_ranks_scores_via_run` inside `TestScannerFacade`, proving ranking comes from `ScannerFacade._run` (`custom()` sorts desc and sets `.rank` 1..N).