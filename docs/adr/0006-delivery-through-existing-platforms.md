# ADR 0006: Deliver through existing sending platforms; no SMTP

- **Status:** Accepted
- **Date:** 2026-10-17

## Context

Running email infrastructure well (deliverability, warm-up, bounces, compliance) is its own
discipline and outside the harness's purpose.

## Decision

- EmailBison and Instantly handle sending. The harness does not build SMTP, sequencing or mailbox
  infrastructure.
- The harness prepares approved drafts, checks delivery authorization, hands off to the platform
  through a thin adapter, and records intent, receipts and outcomes.
- Until a real adapter exists and passes its tests, delivery is simulated (`delivery.simulated`).

## Consequences

- Delivery adapters stay small and replaceable.
- The harness cannot promise exactly-once external sends. Adapters use idempotency keys and the
  platform's own deduplication where available, and reconcile uncertain outcomes before any
  retry.
