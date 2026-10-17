# ADR 0002: Python 3.12+, Pydantic, uv and pytest

- **Status:** Accepted
- **Date:** 2026-10-17

## Context

The harness needs typed, validated data contracts and a reproducible development environment.
Related existing tools are written in Python.

## Decision

- Python 3.12 or later. 3.12 is the baseline that is tested, not a promise that every later
  version works.
- Pydantic v2 for contracts and validation.
- uv for environments, dependency resolution and the lockfile (`uv.lock`, committed).
- pytest for tests.

## Consequences

- `uv sync` reproduces the environment from the lockfile.
- Pydantic validates application shapes. It does not replace database-side validation; see
  [ADR 0003](0003-systems-of-record.md).
- Linting and type checking are proposed separately in
  [ADR 0012](0012-contract-conventions.md).
