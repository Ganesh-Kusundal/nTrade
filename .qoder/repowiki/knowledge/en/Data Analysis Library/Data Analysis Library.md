---
kind: external_dependency
name: Data Analysis Library
slug: pandas
category: external_dependency
category_hints:
    - framework_behavior
scope:
    - '**'
source_files:
    - pyproject.toml
---

### Identity & Role
Primary data manipulation library for financial time series analysis, option chain processing, and historical data handling.

### Integration Points
- Historical data storage and manipulation in `ntrade/domain/market/history.py`
- Option chain DataFrame operations in `ntrade/domain/instruments/derivatives.py`
- Portfolio analytics and reporting throughout domain layer

### Usage Pattern
Used for DataFrame operations, time series resampling, and financial calculations. Core dependency for all analytical functionality.