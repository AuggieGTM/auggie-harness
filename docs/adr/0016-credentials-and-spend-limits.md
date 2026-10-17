# ADR 0016: Per-customer credentials and run spend limits

- **Status:** Accepted
- **Date:** 2026-10-17

## Context

M3 makes the harness's first paid calls to external providers. [ADR 0004](0004-customer-isolation.md)
gives every scope its own credential namespace, and [ADR 0008](0008-treg-for-acquisition.md) says
Treg credentials resolve per scope. The M3 gate asks for per-customer credentials and spend bounded
per run. Treg enforces a hard ceiling per call, but its per-customer budgets are documented as
advisory under concurrency, so the bound on a run has to be the harness's own.

## Decision

**Credentials**

- Secrets live in files outside any repository, one folder per credential namespace:
  `$AUGGIE_CREDENTIALS_DIR/<credential_namespace>/<name>`, by default under
  `~/.config/auggie/credentials`. A file that is not owned by the current user, or that the group
  or others can read, is refused.
- A scope resolves secrets only from its own namespace. There is no default, shared or
  logged-in-CLI fallback: a missing secret stops the call before anything is sent.
- Secret values never enter records, events, logs, exception messages or CLI output. Records name
  the provider and route that were used, never a credential.
- External calls happen only in scopes whose `execution.external_providers` is true.

**Spend**

- `ExecutionBoundary` gains `max_run_spend_micro_usd`, an integer that defaults to 0. Zero allows
  no paid calls.
- Every paid call states its own ceiling, which the adapter sends to the provider (for Treg,
  `X-Treg-Route-Max-Cost`). Before the call, the run reserves that ceiling; a call that could take
  the run past its limit is refused before anything is sent. Afterwards the reservation settles
  to the charge the provider reports (for Treg, `X-Treg-Cost-Micro`). A charge that is not
  reported counts as the full ceiling.
- The settled charge and the provider's call ID are recorded on the event that records the
  result, so a run's spend can be traced call by call.
- The spend ledger lives for one run, in the process that runs it. It is not a scheduler
  ([ADR 0007](0007-single-task-scheduler.md)).

## Consequences

- A run cannot spend more than its limit, provided each provider honors its per-call ceiling.
  Treg documents its ceiling as hard: a call over it is refused with nothing charged.
- Deployments create the credential folders and files. The harness reads secrets and never
  writes them. Rotating a secret means replacing its file.
- Adding a field with a default to `ExecutionBoundary` is a compatible change under
  [ADR 0012](0012-contract-conventions.md): stored scopes still validate.
