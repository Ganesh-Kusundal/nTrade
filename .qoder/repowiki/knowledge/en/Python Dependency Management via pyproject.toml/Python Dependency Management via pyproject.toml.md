---
kind: dependency_management
name: Python Dependency Management via pyproject.toml
category: dependency_management
scope:
    - '**'
source_files:
    - pyproject.toml
    - skills-lock.json
---

This repository manages Python dependencies exclusively through a single `pyproject.toml` file using setuptools as the build backend. There is no lockfile, no vendoring, and no private registry configuration.

**System used**: PEP 517/518 `pyproject.toml` with `setuptools.build_meta` (>=68) as the build backend. Dependencies are declared under `[project]` and optional dependency groups under `[project.optional-dependencies]`. The project requires Python >=3.10.

**Core dependencies**:
- Runtime: `pandas>=2.0`, `numpy>=1.24`, `python-dotenv>=1.0`, `Dhan-Tradehull>=3.3.2`
- Optional `dev`: `pytest>=8.0`
- Optional `ui`: `fastapi>=0.110`, `uvicorn[standard]>=0.29`, `websockets>=12.0`, `pywebview>=5.0`

**Conventions observed**:
- All version pins use minimum-only constraints (`>=X.Y`), never upper bounds or exact versions — this allows pip to resolve the latest compatible release at install time.
- No `requirements.txt`, `poetry.lock`, `Pipfile`, or `uv.lock` exists; dependency resolution is not pinned.
- Package discovery is configured via `[tool.setuptools.packages.find]` with `include = ["ntrade*"]`.
- Test configuration lives in `[tool.pytest.ini_options]` with `testpaths = ["tests"]`.
- A separate `skills-lock.json` tracks AI skill definitions (not Python packages) from GitHub sources.

**Constraints**: No vendor directory, no private PyPI index, no environment-specific dependency files. Development and UI extras are explicitly separated into optional dependency groups rather than being installed by default.