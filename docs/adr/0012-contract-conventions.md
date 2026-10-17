# ADR 0012: Contract conventions and development checks

- **Status:** Accepted
- **Date:** 2026-10-17

## Context

Contracts are shared by every component that will exist, including storage, adapters, judgment,
review and delivery. Their conventions are expensive to change once data has been stored.

## Decision

**Contracts**

- Immutable (`frozen`), with unknown fields rejected (`extra="forbid"`). A change is a new
  record, never an edit.
- Each top-level record has `schema_version` as a literal. Incompatible changes bump it.
- Timestamps must be timezone-aware and are normalized to UTC.
- Identifiers are opaque strings. The Store chooses the concrete form, such as the string form
  of a MongoDB ObjectId.
- Digests use the form `sha256:<hex>`.
- Enumerations are closed `Literal` sets. Adding a value is a code change and is checked against
  stored data.
- Event types form a closed set. Each maps to one loop stage and declares the record kinds it
  must reference.
- Outcomes are `outcome.recorded` events for now. They may become a first-class contract when
  learning work begins.
- No I/O in contracts. Checks that span records are pure functions (`verify_citations`,
  `ensure_in_scope`, `check_isolated`).

**Development checks**

- `ruff` for linting and formatting, `mypy --strict` with the Pydantic plugin, and `pytest`.
  All must pass before a commit.

## Consequences

- Records are safe to share between components and to cache.
- Some flexibility is traded for early errors. For example, a new event type needs a code change.
- This is the reference for the Store's database validators.
