---
kind: build_system
name: Python Package Build & Development Workflow
category: build_system
scope:
    - '**'
source_files:
    - pyproject.toml
    - scripts/benchmark_latency.py
    - scripts/live_runner_run.py
    - scripts/ema_cross_run.py
    - scripts/paper_gate_run.py
    - .agents/skills/dhan-tradehull/references/deployment.md
---

The nTrade project uses a minimal, modern Python packaging and development setup centered on `pyproject.toml` with setuptools as the build backend. There is no Makefile, Dockerfile, CI pipeline, or dedicated release script in the repository.

**Build system**: The project is built via `setuptools.build_meta` (declared in `[build-system]`) with `setuptools>=68`. The package name is `ntrade`, version `0.1.0`, requiring Python `>=3.10`. Dependencies are declared under `[project]` (`pandas`, `numpy`, `python-dotenv`, `Dhan-Tradehull>=3.3.2`). Optional dev dependencies (`pytest>=8.0`) live under `[project.optional-dependencies]`. Package discovery is configured via `[tool.setuptools.packages.find]` to include `ntrade*`.

**Testing**: pytest is the test runner, configured through `[tool.pytest.ini_options]` with `testpaths = ["tests"]`. The `tests/` directory contains ~50 test files covering unit tests for brokers, domain models, events, engines, kernel resilience, replay/backtest, and integration tests for the full feed→kernel→strategy→risk→execution pipeline. Tests are run via `.venv/bin/python -m pytest` (a local virtual environment is present at `.venv/`).

**Development scripts**: The `scripts/` directory holds standalone entrypoints used to exercise the framework rather than automated build steps:
- `benchmark_latency.py` — measures kernel event-pipeline latency and writes results to `.benchmarks/latency.json`
- `live_runner_run.py` — runs the LiveRunner harness in synthetic or live mode against Dhan
- `ema_cross_run.py`, `paper_gate_run.py`, `live_read_check.py`, `live_smoke.py` — ad-hoc runners for strategies and smoke tests
These scripts use `sys.path.insert(0, ...)` to import from the repo root directly, indicating they are intended to be executed from within a local `.venv` environment rather than an installed package.

**Packaging/installation**: No `setup.py`, `MANIFEST.in`, wheel/sdist publishing, or CI publish step exists. The project appears to be developed and consumed as an editable/local install via `pip install .` or by running scripts directly from the source tree with a virtual environment.

**Deployment guidance**: Deployment instructions exist only in agent skill references (`.agents/skills/dhan-tradehull/references/deployment.md`), which describe launching via `setsid`/`nohup` or systemd using `venv/bin/python`. No containerization or automated deployment pipeline is present in the repo.