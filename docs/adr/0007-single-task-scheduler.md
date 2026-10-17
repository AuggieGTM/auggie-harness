# ADR 0007: One task scheduler: Herdr or Foreman

- **Status:** Open
- **Date:** 2026-10-17

## Question

Which system is the single authoritative scheduler for harness work: Herdr or Foreman?

## Already decided

- There is **exactly one** authoritative task scheduler. No competing schedulers, job queues,
  cron loops or background workers may be introduced alongside it.
- Until this is decided, harness code runs as plain sequential calls, with no retries, leases or
  background execution. That is not a scheduler and must not grow into one.

## Options

| Option | What it is |
|---|---|
| Herdr | A terminal workspace manager for AI coding agents. |
| Foreman | The task lifecycle in a separate private project. |

## What would decide it

- Which one owns durable task state, and how restarts, retries and stale work are handled.
- How it respects per-customer isolation ([ADR 0004](0004-customer-isolation.md)).
- Whether it can run without a coding-agent session present.
- For Foreman: a licensing and confidentiality review before any code is reused
  ([ADR 0010](0010-public-repository-boundary.md)).

## Consequences of leaving it open

Work that needs background or recurring execution waits for this decision. The first synthetic
vertical slice does not need it.
