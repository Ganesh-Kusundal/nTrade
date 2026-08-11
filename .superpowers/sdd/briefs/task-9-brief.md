# Task 9 (T-017): Arm scanner M6 throttle + dedupe `Scanner.top` ranking

**Goal:** Stop hot loops re-scanning the full universe every tick (M6). `ScannerFacade._run` (ntrade/domain/scanner.py:136-165) already throttles via `scanner.rate_limit_seconds`, but every built-in keeps the class default `0.0` (no throttle armed). Also, `Scanner.top` (scanner.py:64-70) duplicates `_run`'s ranking/throttle logic and bypasses the facade — a divergent copy.

## Verified facts
- `Scanner.rate_limit_seconds: float = 0.0` (scanner.py:57); built-ins inherit it.
- `ScannerFacade._run` (136-165) is the canonical rank+throttle+cache path (ranks by score desc, sets `.rank` 1..N).
- `Scanner.top` (64-70) has exactly ONE caller repo-wide: `tests/test_scanner.py:120` (`test_top_ranks_by_score`, uses a custom domain Scanner). No production caller.
- Built-in scanners: `ntrade/scanners/builtin.py` has `GapScanner`(42), `VolumeSpikeScanner`(76), `MomentumScanner`(119), `BreakoutScanner`(163), `ImbalanceScanner`(227). (Line numbers approximate — verify.)

## Note — plan inconsistency, resolved as follows
The plan's Step-1 test asserts volume/Momentum/Breakout all have `rate_limit_seconds > 0`, but its Step-3 only arms Momentum. Resolve by arming all THREE full-universe scanners (Volume, Momentum, Breakout) with `rate_limit_seconds = 30.0`. Leave GapScanner/ImbalanceScanner at their base `0.0` (out of scope; they don't iterate the full tick universe the same way).

## Changes
### 1. `ntrade/scanners/builtin.py`
Add a class attribute to each of `MomentumScanner`, `VolumeSpikeScanner`, `BreakoutScanner`:
```python
rate_limit_seconds = 30.0   # M6: don't rescan the full universe every tick
```
(place as a class variable near the top of each class body).

### 2. `ntrade/domain/scanner.py` — remove the divergent `Scanner.top`
Delete the `top` method (scanner.py:64-70). The canonical rank cumulative path is `ScannerFacade._run`; `Scanner.scan` remains the raw abstract scan. Update the `Scanner` ABC docstring if it mentions `top`.

### 3. `tests/test_scanner.py`
- ADD: `test_builtin_scanners_arm_m6_throttle` asserting the three armed classes have `rate_limit_seconds` > 0.
- REPLACE `test_top_ranks_by_score` (currently the only `top` caller) with a facade-path test proving ranking now comes from `_run` (canonical):
```python
def test_facade_ranks_scores_via_run(self):
    session = _make_session_with_instruments()
    facade = ScannerFacade(session)

    class Ranker(Scanner):
        name = "ranker"
        def scan(self, session, **kw):
            return [ScannerResult(instrument=MagicMock(), scanner_name=self.name,
                                  score=s, signal="BUY")
                    for s in (1.0, 5.0, 3.0, 9.0, 2.0)]

    results = facade.custom(Ranker())
    assert [r.score for r in results[:3]] == [9.0, 5.0, 3.0]
    assert [r.rank for r in results[:3]] == [1, 2, 3]
```
(It lives inside `TestScannerFacade`; if `custom()` sorts desc and sets ranks, this holds — verify against `_run`, lines 158-161.)

## Verify
```
./.venv/bin/python -m pytest -q
```
Expected: 630 (baseline) + net (add 1 test, and test_top renamed/replaced so count roughly stays). Report the actual number.

## Commit
```
git add ntrade/scanners/builtin.py ntrade/domain/scanner.py tests/test_scanner.py
git commit -m "T-017 arm M6 scanner throttle; dedupe Scanner.top ranking"
```
Stage ONLY files you changed. Do NOT stage unrelated uncommitted working-tree files (`git status` first). Commit subject exactly `T-017 arm M6 scanner throttle; dedupe Scanner.top ranking`.

## Report
Write `.superpowers/sdd/briefs/task-9-report.md: commit hash, `git show --stat`, full suite count, confirmation that `Scanner.top` had only that one test caller, and which scanners you armed (and the raw values).