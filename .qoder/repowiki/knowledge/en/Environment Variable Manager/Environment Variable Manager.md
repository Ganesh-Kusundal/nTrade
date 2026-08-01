---
kind: external_dependency
name: Environment Variable Manager
slug: python-dotenv
category: external_dependency
category_hints:
    - client_constraint
scope:
    - '**'
source_files:
    - pyproject.toml
---

### Identity & Role
Library for loading environment variables from `.env` files into Python applications.

### Integration Points
- Configuration loading in authentication modules
- Credential management for broker connections
- Development and deployment configuration

### Usage Pattern
Loads trading credentials, API keys, and configuration parameters from `.env` file. Used primarily in authentication flow to access Dhan credentials securely.