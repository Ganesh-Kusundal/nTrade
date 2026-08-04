---
kind: build_system
name: Python Package Build & Pre-Deploy Validation Pipeline
category: build_system
scope:
    - '**'
source_files:
    - pyproject.toml
    - scripts/pre_deploy_check.py
    - scripts/paper_gate_run.py
    - scripts/live_read_check.py
    - scripts/live_smoke.py
---

The ntrade project uses a minimal Python packaging and validation setup with no traditional Makefile, Dockerfile, or CI pipeline files in the repository. The build system is centered around `pyproject.toml` with setuptools as the build backend.

**Build System**: Uses PEP 517/518 compliant `pyproject.toml` with setuptools (>=68) as the build backend. The package name is `ntrade` version `0.1.0`, requiring Python >=3.10. Dependencies are declared in the `[project]` section including pandas, numpy, python-dotenv, and Dhan-Tradehull>=3.3.2. Optional dependencies are split into `dev` (pytest) and `ui` (FastAPI, uvicorn, websockets, pywebview).

**Package Structure**: Setuptools is configured to include only packages matching `ntrade*` pattern via `[tool.setuptools.packages.find]`. No `setup.py` exists — pure declarative configuration.

**Testing Framework**: pytest is configured via `[tool.pytest.ini_options]` with testpaths set to `tests/`. Tests are organized under a flat `tests/` directory with individual test files for each module.

**Pre-Deploy Validation Pipeline**: The project implements a comprehensive pre-deploy gate system through `scripts/pre_deploy_check.py` that orchestrates three validation stages:
1. Token freshness check (JWT expiry validation)
2. Paper gate simulation (`scripts/paper_gate_run.py`) - runs strategy over historical data with synthetic feed
3. Live read-only checks (`scripts/live_read_check.py`) - validates all broker API endpoints without placing orders
4. Live smoke test (`scripts/live_smoke.py`) - exercises core framework functionality against real Dhan API

Each script exits with appropriate codes (0 for pass, 1 for fail) enabling CI integration. The pre-deploy check supports strict mode that fails on DEGRADED status rows.

**Development Workflow**: Scripts use direct Python execution via `.venv/bin/python` paths. Environment configuration is handled through `.env` files and shared token stores. No containerization or cross-compilation is present.

**Version Management**: Single version string `0.1.0` in `pyproject.toml` with no automated version bumping scripts visible.