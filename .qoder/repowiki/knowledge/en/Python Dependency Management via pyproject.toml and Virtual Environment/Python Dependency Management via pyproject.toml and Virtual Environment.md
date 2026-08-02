---
kind: dependency_management
name: Python Dependency Management via pyproject.toml and Virtual Environment
category: dependency_management
scope:
    - '**'
source_files:
    - pyproject.toml
    - skills-lock.json
    - .venv
---

The nTrade Trading Framework manages Python dependencies using a single declarative manifest and a local virtual environment, with no vendoring or private registry configuration.

**System/approach:**
- `pyproject.toml` is the sole dependency declaration file, using setuptools as the build backend (`setuptools.build_meta`) with `setuptools>=68` required for building.
- Runtime dependencies are pinned with minimum versions: `pandas>=2.0`, `numpy>=1.24`, `python-dotenv>=1.0`, and `Dhan-Tradehull>=3.3.2`. The project targets `requires-python = ">=3.10"`.
- Optional dev dependencies are declared under `[project.optional-dependencies]` with `pytest>=8.0` in the `dev` extra.
- A `.venv/` directory exists at the repository root, indicating a local virtual environment is used for isolation; it appears empty/unpopulated in this snapshot.
- No lockfile (e.g., `poetry.lock`, `uv.lock`, `requirements.txt`) is present, so dependency resolution is not deterministic across environments.
- No `setup.py`, `Pipfile`, `pyproject.toml` extras beyond `dev`, or private PyPI index configuration is found.

**Key files:**
- `pyproject.toml` — project metadata, runtime and dev dependencies, package discovery (`include = ["ntrade*"]`), and pytest config (`testpaths = ["tests"]`).
- `skills-lock.json` — locks AI agent skill definitions from GitHub (`Imran-Tradehull/tradehull-dhan-live-algo-skills`); this is separate from Python package dependencies.
- `.venv/` — local Python virtual environment directory.

**Architecture and conventions:**
- Dependencies are declared as open-ended minimum version ranges (`>=X.Y`), allowing pip to resolve the latest compatible version at install time rather than pinning exact versions.
- Package discovery is scoped to the `ntrade*` namespace via setuptools' `packages.find`, keeping the distribution focused on the framework code.
- Testing dependencies are isolated into an optional `dev` extra, so end users installing the package do not pull in pytest by default.
- There is no evidence of vendoring third-party libraries, use of a private PyPI mirror, or environment-specific dependency overrides.