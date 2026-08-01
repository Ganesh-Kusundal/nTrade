---
kind: dependency_management
name: Python dependency management via pyproject.toml and skills-lock.json
category: dependency_management
scope:
    - '**'
source_files:
    - pyproject.toml
    - skills-lock.json
---

This repository manages dependencies using two complementary mechanisms:

1. **Runtime Python dependencies** are declared in `pyproject.toml` under the `[project]` section using PEP 621 metadata. The project requires Python ≥3.10 and declares four runtime dependencies with minimum version pins: `pandas>=2.0`, `numpy>=1.24`, `python-dotenv>=1.0`, and `Dhan-Tradehull>=3.3.2`. Development-only dependencies (currently just `pytest>=8.0`) are listed under `[project.optional-dependencies]` as `dev`. The build system uses `setuptools>=68` with `setuptools.build_meta` as the backend, and packages are discovered via `tool.setuptools.packages.find` including only the `ntrade*` namespace.

2. **Agent skill definitions** are versioned through `skills-lock.json`, which pins the `dhan-tradehull` skill sourced from the GitHub repository `Imran-Tradehull/tradehull-dhan-live-algo-skills` at a specific commit hash (`computedHash`). This lockfile ensures reproducible skill content for AI agents consuming the SKILL.md reference documents.

There is no `requirements.txt`, `Pipfile`, `poetry.lock`, or `uv.lock` file — all dependency declarations flow through `pyproject.toml`. No vendoring strategy is used; the `.venv` directory exists but appears empty in this snapshot. There is no private PyPI registry configuration, no `pip.conf`, and no `setup.cfg` present. Dependency updates are managed by editing the version constraints directly in `pyproject.toml`.