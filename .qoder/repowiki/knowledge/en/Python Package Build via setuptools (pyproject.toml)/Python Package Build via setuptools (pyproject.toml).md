---
kind: build_system
name: Python Package Build via setuptools (pyproject.toml)
category: build_system
scope:
    - '**'
source_files:
    - pyproject.toml
---

The ntrade project uses a minimal, modern Python packaging setup driven entirely by `pyproject.toml` with `setuptools` as the build backend. There are no Makefiles, Dockerfiles, CI pipelines, or shell-based build scripts in the repository.

**Build system and toolchain**
- Backend: `setuptools.build_meta` (requires `setuptools>=68`).
- Packaging is declarative — no `setup.py` exists; package discovery is configured via `[tool.setuptools.packages.find]` including only the `ntrade*` namespace.
- Python version constraint: `>=3.10`.

**Dependencies**
- Core runtime dependencies: `pandas>=2.0`, `numpy>=1.24`, `python-dotenv>=1.0`, `Dhan-Tradehull>=3.3.2`.
- Optional dependency groups:
  - `dev`: `pytest>=8.0` for testing.
  - `ui`: `fastapi>=0.110`, `uvicorn[standard]>=0.29`, `websockets>=12.0`, `pywebview>=5.0` for the optional web UI layer.
- Test configuration is declared under `[tool.pytest.ini_options]` with `testpaths = ["tests"]`.

**Versioning and distribution**
- Version is pinned at `0.1.0` inside `pyproject.toml` under `[project]`. No automated version bumping, changelog generation, or release automation is present.
- Distribution targets are not defined beyond standard `pip install .` / `pip install .[dev]` / `pip install .[ui]` flows.

**Testing**
- pytest is the test runner, invoked through the standard `pytest` command (or `python -m pytest`) against the `tests/` directory. No test orchestration scripts, coverage configuration, or parallel execution flags are present.

**What is absent**
- No `Makefile`, `tox.ini`, `noxfile.py`, or shell scripts for building/testing.
- No `Dockerfile`, `docker-compose.yml`, or containerization artifacts.
- No CI/CD configuration (no `.github/workflows`, `.gitlab-ci.yml`, Jenkinsfile, etc.).
- No cross-compilation, wheel building, or publishing steps.

In short, the build system is intentionally lightweight: install the package with pip (optionally with `[dev]` or `[ui]` extras), run tests with pytest, and execute the provided `scripts/*.py` entry points directly.