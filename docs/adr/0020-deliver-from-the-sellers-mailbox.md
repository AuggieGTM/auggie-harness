# ADR 0020: Deliver from the seller's own mailbox

- **Status:** Proposed
- **Date:** 2026-10-18
- **Would supersede:** [ADR 0006](0006-delivery-through-existing-platforms.md)

## Context

The harness is for a seller prospecting for the company they work at.
[ADR 0006](0006-delivery-through-existing-platforms.md) names EmailBison and Instantly as the
sending platforms. Both are built to send cold email at volume from separate domains and
mailboxes, which is how agencies send for clients. A seller sends from the mailbox their company
gives them, and the company's own tools treat that mail as company mail: CRM activity logging,
retention, compliance review. Outreach sent from a separate cold-email platform sits outside
those tools, and an employer may not allow it.

The rest of ADR 0006 still fits: no SMTP, sequencing or mailbox infrastructure; thin adapters;
delivery simulated until an adapter passes its tests; idempotency keys and reconciliation before
any retry.

The options considered:

1. **Send from the seller's mailbox through its API** (the Gmail API, Microsoft Graph). The
   adapter sends exactly the approved version, and the provider's message ID is the receipt. A
   send-only permission is enough (Gmail's `gmail.send`, Graph's `Mail.Send`), and it cannot
   read the mailbox. It needs an OAuth client, and the company's Google Workspace or Microsoft
   365 administrators decide which apps may use one.
2. **Create a draft in the seller's mailbox** for them to send. The harness could never send by
   itself. But the seller can edit the draft before sending it, so what goes out can differ from
   the approved version, and [ADR 0005](0005-evidence-and-approval-gates.md)'s trace from every
   sent message to an approved draft hash breaks. Creating drafts also needs a broader permission
   than sending.
3. **Hand off to the company's sales engagement platform** (Outreach, Salesloft, HubSpot
   sequences). Many company sellers already send from one, with CRM logging built in. API access
   is usually granted by an administrator, and these platforms run multi-step sequences, while
   the harness approves one message at a time.
4. **Keep EmailBison and Instantly.** They are agency tooling, outside the seller's company's
   systems.

## Decision (proposed)

- Deliver through the platform the seller already sends from for their company. The harness
  does not name one sending platform for everyone.
- The first adapter is option 1: the seller's own mailbox, sending exactly the approved version
  with a send-only permission. Gmail first, then Microsoft 365.
- Sales engagement platforms (option 3) follow, one at a time, where a seller's company uses one
  and grants API access.
- One approved message, one send. The adapter sends nothing on a schedule and no follow-ups, so
  [ADR 0007](0007-single-task-scheduler.md) can stay open.
- The rest of ADR 0006 stands, as listed above.

## Consequences

- Each sent message is an ordinary email from the seller's own mailbox, so whatever their
  company already does with sent mail applies to it.
- An agent's mail connector does not deliver for the harness
  ([ADR 0019](0019-projection-writers-and-agent-connectors.md)). Sending goes through the
  harness's adapter, after its delivery gate.
- A send-only permission cannot read replies, so a person keeps reporting outcomes
  (`auggie outcome`).
- The mailbox credential can send as the seller. It is a per-scope credential
  ([ADR 0016](0016-credentials-and-spend-limits.md)), used only after the delivery gate passes.
  How the seller obtains and stores it, given that the harness never writes secrets, is decided
  with the adapter.
- On acceptance, ADR 0006 is marked superseded, `DeliveryPlatform` drops `emailbison` and
  `instantly` in a new schema version ([ADR 0012](0012-contract-conventions.md)), and the
  architecture and roadmap name the seller's mailbox.
- Not built. Delivery stays simulated.
