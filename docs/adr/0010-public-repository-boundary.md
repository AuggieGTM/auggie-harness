# ADR 0010: Public repository boundary

- **Status:** Accepted
- **Date:** 2026-10-17

## Context

This repository is public and is intended to become open source. Anything committed is published,
and history is hard to retract. A leaked credential stays leaked even after it is deleted from
the repository.

## Decision

- Only material written for this repository, or reviewed for publication, is committed. Ignore
  rules are a backstop, not the boundary.
- Never commit customer or prospect information, credentials, private planning documents, code
  from private repositories, or third-party code without a licensing and confidentiality
  review.
- Examples and fixtures are synthetic: reserved domains (`.example`, `.test`, `.invalid`) and
  invented names. Tests enforce reserved domains under `examples/`.
- Public documents don't link to private repositories or documents.
- Public releases require IP and license, secret and confidentiality checks first.
- `scripts/check_public_boundary.py` checks tracked paths, contents and commit messages. It runs
  in the test suite, in CI and as local pre-commit and commit-msg hooks. Private terms it looks
  for live in a local file outside the repository.

## License

Original code in this repository is released under the MIT License (`LICENSE`), chosen on
2026-10-17. Dependencies keep their own licenses. MongoDB Community Server is licensed separately
(SSPL), so the harness should retrieve official MongoDB images rather than redistribute modified
binaries. Before any bundled distribution, review dependency notices, and get legal review where
the distribution is unusual.

## Consequences

- Contributors need nothing private to work here. Where private context shaped a decision, the
  ADR restates the reasoning.
- If something sensitive is committed by mistake, rotate the credential and rewrite history
  before pushing. Once pushed, treat it as disclosed.
