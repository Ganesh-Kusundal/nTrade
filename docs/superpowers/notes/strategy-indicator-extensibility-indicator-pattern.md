# Indicator addition pattern (Phase A)

Adding a new indicator stays on the existing pattern in the codebase.

## Backend
1. Add a pure function in `ntrade/domain/analytics/indicators.py`.
2. Add one call and one key in `compute_bundle`.
3. Add a test in `tests/test_indicators.py`.

## UI (only if the chart draws the series)
1. Add a series in `ui/src/lib/indicators.ts`.
2. Add a toggle in `ChartPanel` only if the chart actually renders that series.

## Non-goal in Phase A
No indicator plugin system, no per-indicator React registry, no validation framework for indicator declarations.
