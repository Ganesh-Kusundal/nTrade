# Task 7 (T-019): Retire speculative feed mode codes; hardcode full-data code 21

**Goal:** Production (feeds.py + scripts) only uses `mode="full"`, `version="v2"` (defaults). The `ticker`/`quote` mode codes and the `depth`-mode ValueError have zero production callers; `_MODE_CODES`/`_mode_code()` are speculative (D-001 "no speculative flexibility"). Remove them and hardcode subscription code `21` (full data, which already carries depth).

**Decision: retire** (per plan). Do NOT add new ticker/quote paths.

## Verified facts
- `ntrade/sources/dhan_feed.py`:
  - `__init__(self, kernel=None, *, symbols=None, symbol_map=None, feed_factory=None, dhan_context=None, version="v2", mode="full")` stores `self.mode`, `self.version` (docstrings/comments too).
  - `_MODE_CODES = {"ticker": 15, "quote": 17, "depth": 19, "full": 21}`.
  - `_mode_code()` (129-141) returns code / does the depth ValueError.
  - `_subscriptions()` (143-144) uses `self._mode_code()` → `[(exch, sec, self._mode_code()) for ...]`.
  - `_build_feed()` (146-165) constructs dhanhq `MarketFeed(... version=self.version, ...)`.
- `ntrade/runner/feeds.py:17` builds `DhanMarketFeedSource(kernel, **(live_kwargs or {}))` — live_kwargs should only ever carry the defaults; if a config overrides mode/version, that would break — verify none does (grep repo for `Feed`, `mode=`, `version=` in runner/ and any config).

## Change
1. Delete `_MODE_CODES` table, the `_mode_code()` method, and the `mode`/`version` params + `self.mode`/`self.version` storage.
2. `_subscriptions()`: hardcode code `21`:
   ```python
   def _subscriptions(self) -> list:
       return [(exch, sec, 21) for exch, sec in self.symbols]
   ```
3. `_build_feed()`: pass `version="v2"` literally to `MarketFeed(...)` (dhanhq requires a version), or if `_build_feed` looks the version up elsewhere, hardcode "v2". Update the docstring comment if it mentions mode.
4. Remove the now-undocumented `mode`/`version` params from the `__init__` signature.
5. Remove the `"depth-mode is not supported..."` docstring/comment text if it references `mode`.
6. Update ImportError/docstring references to `mode` in the module docstring if any.

## Tests (tests/test_dhan_feed_source.py)
Update the three modes tests:
- `test_source_injects_feed_factory`: REMOVE `mode="full"` from the `DhanMarketFeedSource(...)` call (it already passes `feed_factory=factory, mode="full"` at line 183). Keep the `== [(1, 2885, 21)]` assertion (still holds with hardcoded 21).
- `test_source_default_mode_is_full` (191-192): REPLACE — it asserts `src._mode_code() == 21`. Change to assert the hardcoded subscription code via a rebuilt feed:
  ```python
  def test_feed_always_uses_full_mode_code():
      k = _kernel()
      src = DhanMarketFeedSource(k, symbols=[(1, 2885)],
                                 feed_factory=lambda subs: FakeFeed(subs))
      assert src._subscriptions() == [(1, 2885, 21)]
      assert not hasattr(src, "version")
      assert not hasattr(src, "mode")
  ```
  (Adjust if `_subscriptions()` is private-signature different, but it's `_subscriptions()`.)
- `test_source_bad_mode_raises` (195-198): DELETE entirely (the ValueError path is removed).
- Any other test that passes `mode=`/`version=` to `DhanMarketFeedSource` must drop those arguments. Grep the file for `mode=` / `version=` and update accordingly.

## Verify
```
./.venv/bin/python -m pytest -q
```
Expected: 632 (baseline) + net test-count change (likely 632 - 1 [deleted bad-mode test] + 0/1 adjustments). Report the exact number; it may differ slightly from 632.

## Commit
```
git add ntrade/sources/dhan_feed.py tests/test_dhan_feed_source.py
git commit -m "T-019 retire speculative feed mode codes; hardcode full data code 21"
```
Stage ONLY files you changed. Do NOT stage unrelated uncommitted working-tree files (`git status` first). Commit subject exactly `T-019 retire speculative feed mode codes; hardcode full data code 21`.

## Report
Write to `.superpowers/sdd/briefs/task-7-report.md`: commit hash, `git show --stat`, full suite count, and confirmation that no production caller of `DhanMarketFeedSource` passed `mode=`/`version=` (list any you found).