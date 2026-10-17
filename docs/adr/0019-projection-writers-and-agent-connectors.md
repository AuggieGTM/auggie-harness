# ADR 0019: Projection writers, and what an agent's connectors may do

- **Status:** Accepted
- **Date:** 2026-10-17
- **Supersedes:** [ADR 0018](0018-sheets-projection.md)

## Context

[ADR 0018](0018-sheets-projection.md) made a per-customer Google service account the only way to
write a projection to a sheet. Most GTM engineers who would run this harness already work inside
an agent (Claude, ChatGPT) whose subscription connects to their Google account. A service account
asks them for a Google Cloud project and a long-lived key, and Google organizations often block
key creation by default. Meanwhile [ADR 0005](0005-evidence-and-approval-gates.md) requires that
evidence come from a service or a person, never a model, and [ADR 0003](0003-systems-of-record.md)
makes sheets views of the Store, never its truth.

## Decision

**Projections** (kept from ADR 0018)

- A projection is a pure function of the Store. Its four managed tabs (`auggie: accounts`,
  `auggie: findings`, `auggie: evidence`, `auggie: drafts`) show outcomes, findings, statuses and
  lineage, and no evidence excerpts or draft bodies until Treg's and Exa's retention terms are
  confirmed ([ADR 0017](0017-first-treg-route.md)).
- Each write regenerates the managed tabs completely, as raw text that is never evaluated as a
  formula. Other tabs are never read, changed or removed. Edits in a managed tab are overwritten
  by the next projection. CSV export stays available.

**Two writers, chosen per deployment**

- **An operator's agent connector.** The harness exports the exact workbook as a file. The
  operator's agent writes it to the sheet with the operator's own connector, as raw values, then
  reads the managed tabs back into a file, and the harness compares the two cell by cell. Only a
  match counts as a projection. This writes as the operator, needs an agent session, and is the
  default for supervised work.
- **A per-customer service account**, as ADR 0018 described: its key in the scope's credential
  folder, calls only to Google's token endpoint and the Sheets API, no redirects, nothing quoted
  from Google's replies. This is for unattended runs, which wait for the scheduler decision
  ([ADR 0007](0007-single-task-scheduler.md)), and for customers who want their sheet written only
  by an identity scoped to them.

**What an agent's connectors may do**

- They may write and read back **views**, as above.
- They never **acquire evidence**. Fetching a page through an agent's MCP or connector and
  handing its text to the harness would put a model between the source and the record. Evidence
  is acquired only by the harness's own adapters, with the scope's own credentials
  ([ADR 0016](0016-credentials-and-spend-limits.md)).
- They never **record approvals**. A person approves through the harness
  ([ADR 0005](0005-evidence-and-approval-gates.md)).

## Consequences

- A GTM engineer can see a projection in their own sheet with no Google Cloud setup.
- The read-back check catches a write that went wrong, such as a truncated or reordered tab. It
  relies on the agent saving what the connector returned, so it guards against mistakes, not
  against a dishonest agent. The Store stays the record, and a view can always be regenerated.
- A connector writes as the operator, so it offers no per-customer isolation of Google access.
  Deployments that need that use the service account.
- Whether a consumer subscription's terms allow automated client work through its connectors is
  part of the open question on provider usage terms.
