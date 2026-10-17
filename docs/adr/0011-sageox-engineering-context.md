# ADR 0011: SageOx holds engineering context, not customer intelligence

- **Status:** Accepted
- **Date:** 2026-10-17

## Context

Maintainers and their coding agents share engineering context (decisions, rationale, lessons,
session history) through a private SageOx team. Shared context is useful, and it is also a place
where sensitive material could leak.

## Decision

- SageOx holds reusable engineering context only.
- Customer strategy, research, transcripts, contact data and credentials stay inside each
  customer's separately authorized boundary ([ADR 0004](0004-customer-isolation.md)). They never
  go into SageOx.
- Context in SageOx is not approval of execution policy. A decision binds this repository only
  once it is recorded here as an ADR ([ADR 0001](0001-record-decisions.md)).
- Contributors do not need SageOx.
- SageOx state stays out of this public repository. `.sageox/` and the `.claude/` agent hooks
  are local to each maintainer's clone and ignored by Git, because they name private teams and
  people. No commit, pull request or document links to a SageOx session, recording or dashboard.
- SageOx attribution is turned off for this repository, and its `prepare-commit-msg` hook stays
  disabled in maintainers' clones, because it adds private session links to commit messages.
  Session recording itself is unaffected.

## Consequences

- Agents primed from SageOx must still check ADR status before treating anything as settled.
- Naming a SageOx team after the practice does not by itself isolate anything.
