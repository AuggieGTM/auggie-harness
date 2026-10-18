# ADR 0020: One delivery adapter per sending platform, the seller's mailbox first

- **Status:** Accepted
- **Date:** 2026-10-18
- **Supersedes:** [ADR 0006](0006-delivery-through-existing-platforms.md)

## Context

The harness is for a seller prospecting for the company they work at.
[ADR 0006](0006-delivery-through-existing-platforms.md) names EmailBison and Instantly as the
sending platforms. Both are built for cold email at volume, typically from dedicated sending
domains and mailboxes. That suits a deployment that runs its outreach that way, but a seller at
a company usually sends from the mailbox the company gives them, and the company's own controls
(CRM activity logging, retention, compliance review) are set up around that mail. An employer
may not allow outreach from a separate platform, or may require it.

The rest of ADR 0006 still fits: no SMTP, sequencing or mailbox infrastructure; thin adapters;
delivery simulated until an adapter passes its tests; idempotency keys and reconciliation before
any retry.

The ways to reach a seller's mailbox differ in what they let the harness do and what they
return:

1. **Send through the mailbox's API.** Gmail's `users.messages.send` accepts the send-only
   `gmail.send` scope ("Send email on your behalf", which Google classes as sensitive) and returns
   the sent message, with its ID. Microsoft Graph's `sendMail` needs only `Mail.Send`, and
   returns `202 Accepted` with no body: no message ID, and acceptance does not mean processing
   has finished. Neither send-only permission can read the mailbox, so neither can check the
   Sent folder after a response is lost. Both need an OAuth client, and a company's Google
   Workspace or Microsoft 365 administrators can decide which apps may use one.
2. **Create a draft for the seller to send.** A draft-only adapter would not implement sending,
   but the permissions are wider, not narrower: Gmail's drafts take `gmail.compose` ("Manage
   drafts and send emails", restricted) or broader, and Graph's take `Mail.ReadWrite`, which
   reads and changes the mailbox's mail, though it does not itself send. The seller can also
   edit the draft before sending it, so what goes out can differ from the approved version.
   [ADR 0005](0005-evidence-and-approval-gates.md) ties every sent message to an approved
   draft hash, which an edited draft no longer matches.
3. **Hand off to the company's sales engagement platform** (Outreach, Salesloft, HubSpot
   sequences). Many companies' sellers send from one, often with CRM logging built in. API
   access and plans vary by company, and these platforms run multi-step sequences, while the
   harness approves one message at a time.

## Decision

- Delivery has one thin adapter per sending platform. Each deployment uses the platform it
  already sends from, and a delivery approval names that platform, as it does today.
- The first adapter is the seller's own mailbox through its API (option 1), with send-only
  permissions: Gmail's `gmail.send`, then Microsoft 365's delegated `Mail.Send` for the
  signed-in seller. Graph's application permission is not used, because on its own it is not
  limited to one mailbox.
- This harness is for sellers, so the seller's mailbox is the delivery it plans. Platforms built
  for outreach from dedicated sending domains, such as Instantly and EmailBison, are not planned
  here. The adapter interface and their `DeliveryPlatform` values stay, so a deployment that
  sends that way can add one.
- Sales engagement platforms (option 3) follow one at a time, where a company uses one and
  grants API access.
- The adapter submits exactly the approved subject, body and recipient. One approved message is
  one send: nothing on a schedule, no follow-ups, and no automatic retry, so
  [ADR 0007](0007-single-task-scheduler.md) can stay open.
- A send whose outcome is unknown, such as a lost response, is recorded as unknown and never
  retried by the harness. With a send-only permission the harness cannot check, so the seller
  looks in their Sent folder and records what happened.
- The rest of ADR 0006 stands, as listed above.

## Consequences

- Receipts differ by platform: a Gmail send records the message ID; a Graph send records only
  that Microsoft accepted it.
- A message sent from the seller's mailbox through its API is more likely to fall under their
  company's existing controls, but not certain to. Each deployment checks how its CRM logging,
  retention and compliance tools treat API-sent mail, since some log only mail sent through a
  mail-client add-in. Tenant rules, such as signatures or disclaimers, may still change what the
  recipient sees.
- An agent's mail connector does not deliver for the harness
  ([ADR 0019](0019-projection-writers-and-agent-connectors.md)). Sending goes through the
  harness's adapter, after its delivery gate.
- A send-only permission cannot read replies, so a person keeps reporting outcomes
  (`auggie outcome`).
- The mailbox credential can send as the seller. It is a per-scope credential
  ([ADR 0016](0016-credentials-and-spend-limits.md)), used only after the delivery gate passes.
  OAuth tokens are renewed, and the harness never writes secrets, so how the seller obtains,
  stores and renews this one is decided with the adapter. If the harness would have to write it,
  that needs a change to ADR 0016 first.
- When the mailbox adapter is built, `DeliveryPlatform` gains values for the mailbox platforms,
  and `emailbison` and `instantly` stay ([ADR 0012](0012-contract-conventions.md)).
- Not built. Delivery stays simulated.
