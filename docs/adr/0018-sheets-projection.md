# ADR 0018: Read-only Google Sheets projection

- **Status:** Superseded by [ADR 0019](0019-projection-writers-and-agent-connectors.md)
- **Date:** 2026-10-17

## Context

[ADR 0003](0003-systems-of-record.md) makes Google Sheets and Docs customer-facing views of the
Store, never a source of truth: losing one loses no data. M3 calls for a first read-only
projection. Writing to Google needs credentials, and the
[architecture](../architecture.md) lists Google token refresh as an open question. Treg's and
Exa's retention terms for fetched content are not yet confirmed
([ADR 0017](0017-first-treg-route.md)).

## Decision

- **A projection is a pure function of the Store.** `project(store)` returns a workbook: named
  tabs, each a header row and data rows of plain text, built by walking the event log to the
  records it references. The same workbook can be written as CSV files or to a Google Sheet.
- **The managed tabs** are `auggie: accounts`, `auggie: findings`, `auggie: evidence` and
  `auggie: drafts`. They show outcomes, findings and rationales, source URLs, retrieval and
  validation statuses, draft versions, approvals, deliveries and outcomes. They show **no
  evidence excerpts and no draft bodies**, so no provider content is copied into Google until
  the retention terms are confirmed.
- **Writing to Google uses a per-customer service account.** Its key is a file in the scope's
  credential folder ([ADR 0016](0016-credentials-and-spend-limits.md)). The customer shares one
  spreadsheet with that service account; its ID is given per run on the command line and never
  stored in this repository. `google-auth` signs and refreshes the account's short-lived tokens,
  so there are no user tokens to keep fresh. The few Sheets API calls are made directly, with
  `requests`. Both come with the optional extra `auggie-harness[google]`.
- **Each write regenerates the managed tabs completely**: missing tabs are added, then each is
  cleared and rewritten. Other tabs in the spreadsheet are never read, changed or removed. Edits
  made in a managed tab are overwritten by the next projection; turning them into proposals is
  later work.
- **Values are written as raw text** (`valueInputOption=RAW`), so nothing in a cell is ever
  evaluated as a formula. CSV export guards cells that a spreadsheet would read as a formula.
- Writing to Google is an external call: it happens only in scopes whose
  `execution.external_providers` is true.

## Consequences

- The projection can be rebuilt at any time from the Store, so a deleted or edited sheet is
  repaired by projecting again.
- Reviewers see statuses and lineage, not page text or draft wording. Reviewing a draft's
  wording happens through the harness until the content terms allow more.
- Service accounts are per customer, like Treg organizations, which keeps one customer's sheet
  out of reach of another customer's credentials.
