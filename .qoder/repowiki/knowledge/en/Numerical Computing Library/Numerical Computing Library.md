---
kind: external_dependency
name: Numerical Computing Library
slug: numpy
category: external_dependency
category_hints:
    - framework_behavior
scope:
    - '**'
source_files:
    - pyproject.toml
---

### Identity & Role
Foundation numerical computing library providing array operations, mathematical functions, and performance optimizations for quantitative calculations.

### Integration Points
- Mathematical computations in indicator engines
- Performance-critical operations in market data processing
- Foundation for pandas operations

### Usage Pattern
Core dependency for numerical computations, typically accessed indirectly through pandas but available for direct use in performance-sensitive code paths.