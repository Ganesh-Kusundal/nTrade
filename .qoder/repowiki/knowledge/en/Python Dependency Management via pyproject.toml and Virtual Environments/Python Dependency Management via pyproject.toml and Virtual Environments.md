---
kind: dependency_management
name: Python Dependency Management via pyproject.toml and Virtual Environments
category: dependency_management
scope:
    - '**'
source_files:
    - pyproject.toml
    - skills-lock.json
    - .venv/
    - ntrade/__init__.py
---

This repository manages Python dependencies using the modern PEP 621 `pyproject.toml` format with setuptools as the build backend. There is no lockfile committed to the repository, no vendoring strategy, and no private package registry configuration.

**System used:**
- **Package manager**: pip (via setuptools build backend) declared in `[build-system]`
- **Dependency declaration**: PEP 621 style in `[project]` and `[project.optional-dependencies]`
- **Virtual environment**: `.venv/` directory present at repository root for local isolation
- **No lockfile**: No `poetry.lock`, `uv.lock`, `requirements.txt`, or `Pipfile.lock` is tracked in version control

**Key files and packages:**
- `pyproject.toml` — single source of truth for runtime and optional dependencies
- `skills-lock.json` — locks AI agent skills (not Python packages) from GitHub sources
- `.venv/` — local virtual environment directory (gitignored)
- `ntrade/__init__.py` — defines `__version__ = "0.2.0"` for the package itself

**Runtime dependencies** (from `pyproject.toml`):
- `pandas>=2.0`, `numpy>=1.24`, `python-dotenv>=1.0`, `Dhan-Tradehull>=3.3.2`

**Optional dependency groups:**
- `dev`: `pytest>=8.0` for testing
- `ui`: `fastapi>=0.110`, `uvicorn[standard]>=0.29`, `websockets>=12.0`, `pywebview>=5.0` for the web interface

**Architecture and conventions:**
- Dependencies use minimum version pinning (`>=X.Y`) rather than exact pins, allowing patch updates but preventing major/minor regressions
- Optional dependencies are split into feature groups (`dev`, `ui`) for modular installation
- The project uses a flat `ntrade*` package discovery pattern via setuptools
- Test configuration is centralized in `[tool.pytest.ini_options]` with `tests` as the test path

**Constraints and observed patterns:**
- No transitive dependency pinning is enforced — only direct dependencies are declared
- No dependency audit tooling (e.g., safety, bandit) is configured in the project
- The `skills-lock.json` file demonstrates a separate locking mechanism for non-Python assets (AI skills), showing awareness of deterministic installs for external resources
- Version numbers are maintained both in `pyproject.toml` (`version = "0.1.0"`) and `ntrade/__init__.py` (`__version__ = "0.2.0"`), indicating potential drift between distribution metadata and runtime version