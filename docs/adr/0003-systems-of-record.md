# ADR 0003: Git for approved strategy, MongoDB per customer for operations, Google for views

- **Status:** Accepted
- **Date:** 2026-10-17

## Context

GTM work mixes slow-changing, reviewed material (ICP, offers, voice, qualification criteria) with
fast-growing operational records (evidence, decisions, approvals, outcomes). Customers also need
readable views. If more than one place can be authoritative for the same fact, they drift apart.

## Decision

- **GitHub holds approved strategy and executable configuration.** Changes go through review.
  Operational records cite the exact revision they used (`StrategyRef`: commit, path, digest).
- **MongoDB Community is the planned operational system of record**, with one database per
  customer, reached only through a typed Store API. No other component talks to MongoDB directly.
- **Google Sheets and Docs are customer-facing views.** They are generated from the Store. Edits
  made there come back as proposals, never as authoritative writes.
- **SageOx holds engineering context**, not operational data; see
  [ADR 0011](0011-sageox-engineering-context.md).

## Consequences

- Any result can be traced to a Git revision plus Store records.
- Contracts do not depend on the MongoDB driver. A Pydantic model is not a MongoDB validator, so
  database validators need their own translation and parity tests.
- Losing a Sheet or Doc loses no data; it can be regenerated.
- The Store API and the MongoDB integration are not built yet.
