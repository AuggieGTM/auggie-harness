# ADR 0001: Record decisions as public ADRs

- **Status:** Accepted
- **Date:** 2026-10-17

## Context

The harness is public and will be built over time by maintainers and coding agents. Some
planning material is private. Contributors and agents still need to know what has been decided,
what is only proposed and what is unresolved, without access to private documents.

## Decision

- Record each decision that affects architecture, data compatibility, installation, extension
  behavior or the public boundary as a short, numbered ADR in `docs/adr/`.
- Use the statuses Accepted, Proposed, Open and Superseded, as defined in the
  [index](README.md).
- ADRs give their rationale in public terms. They do not link to private repositories or
  documents, and they contain no customer information.
- An Accepted ADR is never rewritten in place. A change is a new ADR that supersedes it, and the
  old one is marked Superseded with a link to its replacement.
- Accepting a Proposed ADR, or deciding an Open one, is the maintainer's call.

## Consequences

- Agents can tell settled constraints from proposals by reading the status line.
- A decision made elsewhere, such as in a meeting or in SageOx, is not binding on this repository
  until it is recorded here.
