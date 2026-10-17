# ADR 0015: MongoDB Store layout

- **Status:** Accepted
- **Date:** 2026-10-17

## Context

[ADR 0003](0003-systems-of-record.md) makes MongoDB Community the operational system of record,
with one database per customer, reached only through the typed Store API. The Store's behavior is
defined by its contract tests (`tests/store/`), which the in-memory reference passes. The MongoDB
implementation must pass the same suite, and where it can, the database itself should enforce the
invariants rather than trusting every caller.

## Decision

- **One database per scope**, named by the scope's `data_namespace`. `MongoBackend.open` refuses
  scopes that collide with one already open (`check_isolated`). Each database holds a `_scope`
  marker naming its customer and namespaces; opening it for a different scope is refused, even
  from another process.
- **One collection per record kind**: `evidence`, `qualifications`, `artifacts`, `approvals`,
  `events`. A document is the contract's JSON form plus `_id` and `seq`. Reads validate the
  contract again.
- **Unique indexes carry the invariants:**

  | Index | Guarantees |
  |---|---|
  | `events.idempotency_key` | An event is stored once per key |
  | `events.event_id` | Event IDs are unique |
  | `artifacts (ref.artifact_id, ref.content_hash)` | Each version is stored once |
  | `artifacts (ref.artifact_id, supersedes)` | Versions form one line: one first version, no forks |

  Writing a record whose identity exists loads the stored one: identical is a no-op, different is
  a conflict, as the contract tests require.
- **Write order** comes from a per-database counter stored in each document's `seq`, because
  MongoDB does not guarantee natural order.
- **A validator backstop:** every collection has a `$jsonSchema` validator requiring
  `schema_version` and a `customer_id` equal to the database's customer, so even a write that
  bypasses the Store cannot mix customers. Translating whole contracts into validators is later
  work, with parity tests ([ADR 0003](0003-systems-of-record.md)).
- **No multi-document transactions** for now. There is one sequential writer
  ([ADR 0007](0007-single-task-scheduler.md)), records are append-only, and the unique indexes
  cover the races that matter. A record and its event are two writes; if a run stops between
  them, rerunning the same inputs completes the pair, because every write is idempotent. Revisit
  when a scheduler or concurrent writers arrive.
- **PyMongo**, as the optional extra `auggie-harness[mongodb]`. A core install needs only
  Pydantic. Tests and CI use the official MongoDB image; nothing is redistributed
  ([ADR 0010](0010-public-repository-boundary.md)).

## Consequences

- Isolation rests on separate databases, the scope marker and the validators. Separate database
  users and credentials per customer remain a deployment duty
  ([ADR 0004](0004-customer-isolation.md)).
- A stopped run can leave a record without its event until the step is rerun.
- Every document carries its `schema_version`. There is no migration tooling yet.
