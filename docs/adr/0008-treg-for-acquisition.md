# ADR 0008: Treg for data acquisition, behind an adapter

- **Status:** Superseded by [ADR 0024](0024-careers-snapshots-and-hiring-tallies.md)
- **Date:** 2026-10-17

## Context

Account research needs external data: web pages, search results, company and people enrichment,
and similar sources. Building and maintaining those integrations one by one is costly.

## Decision

- Treg handles data acquisition.
- Treg is reached through an acquisition adapter. Everything it returns is recorded as
  `SourceEvidence`, with the provider, request ID, dates and a content hash.
- Treg credentials resolve per customer scope ([ADR 0004](0004-customer-isolation.md)).

## Consequences

- Provider responses are untrusted data. They never authorize actions or change strategy.
- Treg-specific details stay inside the adapter. Business logic depends on `SourceEvidence`, not
  on provider responses.
- Each Treg route the harness relies on is enabled one at a time, with its own tests. Using Treg
  does not mean its whole catalog is supported.
- Not built yet.
