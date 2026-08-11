# Tick Simulator

<cite>
**Referenced Files in This Document**
- [tick_simulator.py](file://ntrade/sim/tick_simulator.py)
- [synthetic_feed.py](file://ntrade/sources/synthetic_feed.py)
- [market_feed.py](file://ntrade/sources/market_feed.py)
- [candles.py](file://ntrade/domain/market/candles.py)
- [market.py](file://ntrade/events/market.py)
- [clock.py](file://ntrade/kernel/clock.py)
- [test_tick_simulator.py](file://tests/test_tick_simulator.py)
</cite>

## Update Summary
**Changes Made**
- Updated core components section to document the new anchor-gap invariant
- Enhanced detailed component analysis with anchor-gap implementation details
- Added new section on anchor-gap invariant enforcement
- Updated configuration options to include MIN_ANCHOR_GAP parameter
- Enhanced troubleshooting guide with anchor-gap related validation
- Updated data quality validation to include anchor-gap checks

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Anchor-Gap Invariant Enforcement](#anchor-gap-invariant-enforcement)
7. [Dependency Analysis](#dependency-analysis)
8. [Performance Considerations](#performance-considerations)
9. [Troubleshooting Guide](#troubleshooting-guide)
10. [Conclusion](#conclusion)
11. [Appendices](#appendices)

## Introduction
The TickSimulator component generates deterministic, realistic tick sequences from historical 1-minute OHLCV bars while preserving key market microstructure characteristics such as price bounds, extremes, and volume distribution. It is designed to be fully reproducible via a random seed and integrates seamlessly with the synthetic feed system to produce canonical market events identical to live feeds, ensuring zero parity across simulation, replay, and live environments.

**Updated** Enhanced with anchor-gap invariant ensuring minimum separation between high and low price anchors within simulated bars, preventing unrealistic price movements where high and low prices occur at same timestamp.

## Project Structure
The TickSimulator lives under the simulation module and is consumed by the synthetic feed source that publishes canonical market events into the kernel's event bus. The domain layer provides typed wrappers for OHLCV data, and the event layer defines the canonical TickEvent and QuoteEvent used throughout the system.

```mermaid
graph TB
subgraph "Simulation"
TS["TickSimulator<br/>synthesize_1m_ticks"]
ST["SimTick"]
AG["Anchor-Gap<br/>Invariant"]
end
subgraph "Sources"
SF["SyntheticMarketFeedSource"]
MF["MarketFeedSource (base)"]
end
subgraph "Domain"
CS["CandleSeries"]
end
subgraph "Events"
TE["TickEvent"]
QE["QuoteEvent"]
end
subgraph "Kernel"
CLK["TradingClock"]
end
CS --> SF
SF --> TS
SF --> QE
SF --> TE
SF --> CLK
TS --> ST
TS --> AG
```

**Diagram sources**
- [tick_simulator.py:1-87](file://ntrade/sim/tick_simulator.py#L1-L87)
- [synthetic_feed.py:1-77](file://ntrade/sources/synthetic_feed.py#L1-L77)
- [market_feed.py:1-105](file://ntrade/sources/market_feed.py#L1-L105)
- [candles.py:1-67](file://ntrade/domain/market/candles.py#L1-L67)
- [market.py:1-83](file://ntrade/events/market.py#L1-L83)
- [clock.py:1-55](file://ntrade/kernel/clock.py#L1-L55)

**Section sources**
- [tick_simulator.py:1-87](file://ntrade/sim/tick_simulator.py#L1-L87)
- [synthetic_feed.py:1-77](file://ntrade/sources/synthetic_feed.py#L1-L77)
- [market_feed.py:1-105](file://ntrade/sources/market_feed.py#L1-L105)
- [candles.py:1-67](file://ntrade/domain/market/candles.py#L1-L67)
- [market.py:1-83](file://ntrade/events/market.py#L1-L83)
- [clock.py:1-55](file://ntrade/kernel/clock.py#L1-L55)

## Core Components
- SimTick: Immutable dataclass representing a simulated tick with timestamp, price, and quantity.
- synthesize_1m_ticks: Deterministic function that converts a single 1-minute OHLCV bar into a sequence of per-second ticks respecting invariants (open/close anchors, high/low bounds, volume sum).
- MIN_ANCHOR_GAP: Configuration constant defining minimum seconds between high and low price anchors (default: 5).
- SyntheticMarketFeedSource: Consumes OHLCV frames and publishes QuoteEvent per bar and TickEvent per simulated second using the seeded tick simulator.
- MarketFeedSource: Abstract base defining the interface for all market data sources, enabling zero-parity integration.
- CandleSeries: Typed wrapper around OHLCV DataFrame used by sources.
- TradingClock: Deterministic clock abstraction allowing simulation to control time progression.

Key responsibilities:
- Generate realistic intra-bar price paths anchored at open/high/low/close.
- Distribute volume proportionally to absolute price moves.
- Inject bounded noise to mimic microstructure fluctuations.
- Maintain temporal consistency via timestamps derived from the bar start time.
- Provide deterministic outputs through explicit random seeds.
- **New**: Enforce minimum separation between high and low price anchors to prevent unrealistic price movements.

**Updated** Added MIN_ANCHOR_GAP constant and anchor-gap enforcement mechanism.

**Section sources**
- [tick_simulator.py:20-87](file://ntrade/sim/tick_simulator.py#L20-L87)
- [synthetic_feed.py:19-77](file://ntrade/sources/synthetic_feed.py#L19-L77)
- [market_feed.py:23-105](file://ntrade/sources/market_feed.py#L23-L105)
- [candles.py:18-67](file://ntrade/domain/market/candles.py#L18-L67)
- [clock.py:14-55](file://ntrade/kernel/clock.py#L14-L55)

## Architecture Overview
The synthetic feed orchestrates the conversion of OHLCV bars into canonical events. For each bar, it publishes a QuoteEvent and then iteratively publishes TickEvent instances generated by the tick simulator. The kernel's clock can be advanced deterministically to maintain consistent timing across simulation runs.

```mermaid
sequenceDiagram
participant Source as "SyntheticMarketFeedSource"
participant Bus as "Event Bus"
participant Clock as "TradingClock"
participant Sim as "synthesize_1m_ticks"
participant AnchorGap as "Anchor-Gap Check"
participant Events as "TickEvent/QuoteEvent"
Source->>Bus : publish QuoteEvent(bar)
loop For each second in bar
Source->>Sim : synthesize_1m_ticks(ts, o, h, l, c, v, seed, seconds)
Sim->>AnchorGap : validate min gap between hi/lo
AnchorGap-->>Sim : gap validated
Sim-->>Source : list[SimTick]
alt Clock supports set()
Source->>Clock : set(tick.ts)
end
Source->>Bus : publish TickEvent(price, quantity, ts)
end
```

**Diagram sources**
- [synthetic_feed.py:52-77](file://ntrade/sources/synthetic_feed.py#L52-L77)
- [tick_simulator.py:54-87](file://ntrade/sim/tick_simulator.py#L54-L87)
- [clock.py:40-46](file://ntrade/kernel/clock.py#L40-L46)
- [market.py:12-38](file://ntrade/events/market.py#L12-L38)

## Detailed Component Analysis

### TickSimulator: Price Path Generation and Volume Distribution
The core algorithm constructs an intra-bar price path using piecewise-linear interpolation between anchor points (open, high, low, close), injects bounded noise, rounds prices, and distributes volume based on absolute price moves.

```mermaid
flowchart TD
Start(["Function Entry"]) --> Validate["Validate inputs<br/>high >= low<br/>seconds >= 4"]
Validate --> InitRNG["Initialize RNG with seed"]
InitRNG --> CalcMinGap["Calculate min_gap:<br/>max(1, min(MIN_ANCHOR_GAP,<br/>(seconds-3)//2))"]
CalcMinGap --> PickAnchors["Pick distinct hi/lo indices<br/>with min_gap constraint"]
PickAnchors --> Interp["Interpolate prices through anchors"]
Interp --> Noise["Add bounded noise to non-anchor ticks"]
Noise --> Round["Round prices to 2 decimals"]
Round --> PinAnchors["Re-pin anchors to open/high/low/close"]
PinAnchors --> DistVol["Distribute volume proportional to |move|"]
DistVol --> BuildTicks["Build SimTick list with timestamps"]
BuildTicks --> End(["Return ticks"])
```

**Diagram sources**
- [tick_simulator.py:54-87](file://ntrade/sim/tick_simulator.py#L54-L87)

Key implementation details:
- Anchors ensure open/close are fixed and both high/low are touched within the bar.
- **New**: Anchor-gap invariant enforces minimum separation between high and low price anchors using `min_gap = max(1, min(MIN_ANCHOR_GAP, (n - 3) // 2))`.
- Noise amplitude is proportional to bar range (2% of high-low), maintaining realism without violating bounds.
- Volume distribution uses absolute price changes; flat segments receive no volume, aligning with microstructure intuition.
- Rounding occurs before re-pinning anchors to preserve exact high/low values even for non-integer prices.

**Updated** Enhanced with anchor-gap calculation and enforcement logic.

**Section sources**
- [tick_simulator.py:20-87](file://ntrade/sim/tick_simulator.py#L20-L87)

### SyntheticFeed Integration
The synthetic feed bridges OHLCV data and the kernel's event system. It validates input data, publishes quote snapshots per bar, and emits tick events using the seeded simulator. It also synchronizes the kernel's clock when available.

```mermaid
classDiagram
class MarketFeedSource {
+kernel
+attach(kernel)
+bus
+start()
+stop()
}
class SyntheticMarketFeedSource {
+name = "synthetic"
+symbol
+exchange
+data
+seed
+seconds
+ticks_published
+start()
+join(timeout)
+stop()
-_produce()
}
class CandleSeries {
+to_dataframe()
+symbol
+timeframe
+empty
+__len__()
+__getattr__(name)
}
class SimTick {
+ts
+price
+quantity
}
MarketFeedSource <|-- SyntheticMarketFeedSource
SyntheticMarketFeedSource --> CandleSeries : "consumes"
SyntheticMarketFeedSource --> SimTick : "generates via simulator"
```

**Diagram sources**
- [market_feed.py:23-105](file://ntrade/sources/market_feed.py#L23-L105)
- [synthetic_feed.py:19-77](file://ntrade/sources/synthetic_feed.py#L19-L77)
- [candles.py:18-67](file://ntrade/domain/market/candles.py#L18-L67)
- [tick_simulator.py:23-27](file://ntrade/sim/tick_simulator.py#L23-L27)

**Section sources**
- [synthetic_feed.py:19-77](file://ntrade/sources/synthetic_feed.py#L19-L77)
- [market_feed.py:23-105](file://ntrade/sources/market_feed.py#L23-L105)

### Event System Integration
The tick simulator produces SimTick objects, which are converted into canonical TickEvent instances by the synthetic feed. QuoteEvent snapshots provide OHLCV context for downstream consumers.

```mermaid
erDiagram
TICK_EVENT {
string symbol
string exchange
float price
int quantity
string side
string kind
}
QUOTE_EVENT {
string symbol
string exchange
float ltp
float bid
float ask
float open
float high
float low
float prev_close
int volume
int oi
}
SIM_TICK {
datetime ts
float price
int quantity
}
SIM_TICK ||--o{ TICK_EVENT : "converted by feed"
QUOTE_EVENT ||--o{ TICK_EVENT : "contextualized by"
```

**Diagram sources**
- [market.py:12-38](file://ntrade/events/market.py#L12-38)
- [market.py:24-38](file://ntrade/events/market.py#L24-38)
- [tick_simulator.py:23-27](file://ntrade/sim/tick_simulator.py#L23-L27)

**Section sources**
- [market.py:12-38](file://ntrade/events/market.py#L12-38)
- [market.py:24-38](file://ntrade/events/market.py#L24-38)

## Anchor-Gap Invariant Enforcement

The anchor-gap invariant is a critical enhancement that prevents unrealistic price movements by ensuring minimum separation between high and low price anchors within simulated bars. This addresses edge cases where high and low prices could occur at the same timestamp or too close together, which would create artificial spikes in the simulated price path.

### Implementation Details

The anchor-gap enforcement operates through several mechanisms:

1. **Minimum Gap Calculation**: `min_gap = max(1, min(MIN_ANCHOR_GAP, (n - 3) // 2))`
   - Uses `MIN_ANCHOR_GAP = 5` as the baseline minimum separation
   - Scales with bar duration to maintain proportionality
   - Ensures at least 1 second separation for very short bars

2. **Anchor Selection Algorithm**: 
   - Randomly selects high and low anchor positions within the bar
   - Validates separation meets minimum gap requirements
   - Retries anchor selection if gap constraint is violated

3. **Edge Case Handling**:
   - For n=4 bars, maintains lo ≠ hi even when formula yields 0
   - Prevents adjacent anchor placement that could cause unrealistic price jumps
   - Maintains balance between randomness and realism

### Mathematical Foundation

The anchor-gap constraint follows the formula:
```
|min(idx(high) - idx(low))| ≥ max(1, min(MIN_ANCHOR_GAP, (seconds-3)//2))
```

This ensures:
- Minimum 5 seconds separation for standard 60-second bars
- Proportional scaling for different timeframe configurations
- Absolute minimum of 1 second for very short simulations

### Validation and Testing

The anchor-gap invariant is enforced through comprehensive testing:

```python
def test_no_full_range_jump_in_one_second():
    """Non-adjacent anchors prevent full bar-range jumps for n >= 6."""
    # Tests that maximum price jump is less than full bar range
    assert max_jump < bar_range
```

**Section sources**
- [tick_simulator.py:11-20](file://ntrade/sim/tick_simulator.py#L11-L20)
- [tick_simulator.py:64-68](file://ntrade/sim/tick_simulator.py#L64-L68)
- [test_tick_simulator.py:75-96](file://tests/test_tick_simulator.py#L75-L96)

## Dependency Analysis
The tick simulator has minimal external dependencies, relying only on Python's standard library for randomness and datetime operations. Its integration points are well-defined through the synthetic feed and event system.

```mermaid
graph LR
TS["tick_simulator.py"] --> RNG["random.Random"]
TS --> DT["datetime/timedelta"]
TS --> MIN_GAP["MIN_ANCHOR_GAP"]
SF["synthetic_feed.py"] --> TS
SF --> ME["events/market.py"]
SF --> MF["sources/market_feed.py"]
CS["domain/market/candles.py"] --> SF
CLK["kernel/clock.py"] --> SF
```

**Diagram sources**
- [tick_simulator.py:16-20](file://ntrade/sim/tick_simulator.py#L16-L20)
- [synthetic_feed.py:14-16](file://ntrade/sources/synthetic_feed.py#L14-L16)
- [market_feed.py:17](file://ntrade/sources/market_feed.py#L17)
- [candles.py:12](file://ntrade/domain/market/candles.py#L12)
- [clock.py:11](file://ntrade/kernel/clock.py#L11)

**Section sources**
- [tick_simulator.py:16-20](file://ntrade/sim/tick_simulator.py#L16-L20)
- [synthetic_feed.py:14-16](file://ntrade/sources/synthetic_feed.py#L14-L16)
- [market_feed.py:17](file://ntrade/sources/market_feed.py#L17)
- [candles.py:12](file://ntrade/domain/market/candles.py#L12)
- [clock.py:11](file://ntrade/kernel/clock.py#L11)

## Performance Considerations
- Time Complexity: O(n) per bar where n equals the number of seconds (default 60). Price interpolation, noise injection, and volume distribution all scale linearly.
- Memory Usage: Creates a list of SimTick objects per bar; memory footprint is proportional to seconds count.
- Randomness Overhead: Seeded RNG ensures reproducibility with negligible overhead compared to arithmetic operations.
- Volume Distribution: Uses absolute price moves; flat bars result in zero volume distribution except for remainder handling.
- **New**: Anchor-gap validation adds minimal overhead through conditional checks and potential retry loops for anchor selection.

Optimization opportunities:
- Vectorization: For large datasets, consider numpy-based vectorization for price interpolation and volume distribution.
- Lazy Evaluation: Stream ticks instead of materializing full lists for memory-constrained environments.
- Caching: Cache repeated bar patterns if processing identical OHLCV data multiple times.
- **New**: Optimize anchor-gap validation for edge cases with very small timeframes.

## Troubleshooting Guide
Common issues and resolutions:
- Invalid Bar Range: Raises ValueError if high < low. Ensure OHLCV data integrity before processing.
- Insufficient Seconds: Requires seconds >= 4. Adjust configuration for very short timeframes.
- Flat Bars: All ticks have identical prices; volume distribution results in zeros except last tick remainder.
- Non-Deterministic Output: Verify seed parameter is consistent across runs for reproducibility.
- Clock Synchronization: If kernel.clock lacks set() method, time synchronization is skipped but simulation continues.
- **New**: Anchor-Gap Violations: If anchor-gap constraints cannot be satisfied, check bar duration and MIN_ANCHOR_GAP settings.

Validation checklist:
- Confirm one tick per second (60 ticks for default 1-minute bar).
- Verify first tick equals open and last equals close.
- Check all prices stay within [low, high].
- Ensure max/min prices equal high/low respectively.
- Validate sum of quantities equals bar volume.
- Test determinism with same seed produces identical output.
- **New**: Verify minimum separation between high and low anchor timestamps.
- **New**: Check that price jumps don't exceed reasonable thresholds relative to bar range.

**Updated** Added anchor-gap validation and troubleshooting guidance.

**Section sources**
- [test_tick_simulator.py:13-109](file://tests/test_tick_simulator.py#L13-L109)
- [tick_simulator.py:57-60](file://ntrade/sim/tick_simulator.py#L57-L60)

## Conclusion
The TickSimulator provides a robust, deterministic mechanism for converting OHLCV bars into realistic tick sequences while preserving critical market microstructure properties. Its integration with the synthetic feed system enables seamless operation across simulation, replay, and live trading contexts with zero parity. The component's design emphasizes reproducibility through seed management, temporal consistency via timestamp generation, and realistic behavior through bounded noise and volume distribution algorithms.

**Updated** The recent enhancement with anchor-gap invariant enforcement significantly improves the realism of simulated price paths by preventing unrealistic price movements where high and low prices occur at the same or adjacent timestamps. This ensures more accurate market microstructure simulation and better validation of trading strategies against realistic price action patterns.

## Appendices

### Configuration Options
- seed: Integer seed for deterministic random number generation (default: 0)
- seconds: Number of ticks per bar (default: 60 for 1-second ticks over 1-minute bar)
- MIN_ANCHOR_GAP: Minimum seconds between high and low price anchors (default: 5)
- Input validation: high >= low required, seconds >= 4 enforced

### Creating Custom Price Generators
To extend the tick simulator:
1. Implement custom interpolation logic replacing _interp function
2. Modify noise injection parameters in synthesize_1m_ticks
3. Adjust volume distribution strategy in _distribute_volume
4. **New**: Configure MIN_ANCHOR_GAP for desired anchor separation
5. Maintain invariants: open/close anchors, high/low bounds, volume sum, anchor-gap constraints

### Example Usage Patterns
- Basic simulation: Call synthesize_1m_ticks with OHLCV data and default parameters
- Reproducible testing: Use fixed seed values for consistent test results
- Custom timeframe: Adjust seconds parameter for different tick frequencies
- Integration: Feed results into SyntheticMarketFeedSource for event publishing
- **New**: Tune anchor-gap settings for specific market conditions or instrument characteristics

### Data Quality Validation
- Statistical analysis: Compare generated tick statistics against real market data
- Microstructure metrics: Analyze bid-ask spread proxies, volume profiles, and price impact
- Temporal consistency: Verify timestamp ordering and spacing
- Boundary compliance: Ensure all prices remain within specified ranges
- **New**: Anchor-gap validation: Verify minimum separation between high and low price anchors
- **New**: Price movement analysis: Ensure no unrealistic jumps or spikes in price paths

**Section sources**
- [tick_simulator.py:54-87](file://ntrade/sim/tick_simulator.py#L54-L87)
- [synthetic_feed.py:22-31](file://ntrade/sources/synthetic_feed.py#L22-L31)
- [test_tick_simulator.py:13-109](file://tests/test_tick_simulator.py#L13-L109)