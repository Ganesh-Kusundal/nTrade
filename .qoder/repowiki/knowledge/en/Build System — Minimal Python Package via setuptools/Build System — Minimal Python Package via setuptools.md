---
kind: build_system
name: Build System — Minimal Python Package via setuptools
category: build_system
scope:
    - '**'
source_files:
    - pyproject.toml
    - .gitignore
---

The nTrade Trading Framework uses a minimal, convention-driven Python build setup with no dedicated build scripts, CI pipelines, Dockerfiles, or Makefiles. All build and packaging configuration is centralized in `pyproject.toml`.

**System used**: PEP 517/518 build system backed by `setuptools.build_meta` (>=68). The project declares itself as a standard Python package named `ntrade` with version `0.1.0`, requiring Python >=3.10.

**Key files**:
- `pyproject.toml` — sole source of truth for dependencies, package discovery (`include = ["ntrade*"]`), pytest config (`testpaths = ["tests"]`), and build backend.
- `.gitignore` — ignores `.pytest_cache/`, `.venv/`, and other typical Python artifacts.
- `scripts/` — standalone Python entrypoints (`benchmark_latency.py`, `ema_cross_run.py`, `live_read_check.py`, `live_runner_run.py`, `live_smoke.py`, `paper_gate_run.py`) used for ad-hoc execution rather than formal build targets.
- `tests/` — pytest test suite discovered automatically via the `[tool.pytest.ini_options]` section.

**Architecture & conventions**:
- No `setup.py`; all metadata lives in `pyproject.toml` under `[build-system]`, `[project]`, `[project.optional-dependencies]`, and `[tool.*]` sections.
- Dependencies are pinned with minimum versions: `pandas>=2.0`, `numpy>=1.24`, `python-dotenv>=1.0`, `Dhan-Tradehull>=3.3.2`. Development-only dependency `pytest>=8.0` is exposed via the `dev` optional group.
- Package discovery is explicit via `tool.setuptools.packages.find.include = ["ntrade*"]`, ensuring only the `ntrade` package tree is included in distributions.
- Testing is configured declaratively in `[tool.pytest.ini_options]` with `testpaths = ["tests"]`; there is no `tox`, `nox`, or custom test runner script.
- There is no virtual environment management tooling (no `Pipfile`, `poetry.lock`, `requirements.txt`, or `environment.yml`); the presence of `.venv/` in `.gitignore` suggests developers create venvs manually.
- No containerization (no `Dockerfile`, `docker-compose.yml`), no CI/CD configuration (no `.github/workflows`, `.gitlab-ci.yml`, etc.), and no release automation scripts.

**Conventions & constraints**:
- Build/installation follows standard `pip install .` / `pip install -e .[dev]` workflows; no custom make targets or shell scripts are required.
- Tests are run via `pytest` directly against the `tests/` directory with no additional fixtures or configuration beyond what's declared in `pyproject.toml`.
- The absence of any packaging metadata beyond `pyproject.toml` means distribution artifacts (wheels/sdists) would be generated through the default setuptools pipeline without customization.