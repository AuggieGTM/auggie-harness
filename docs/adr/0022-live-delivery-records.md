# ADR 0022: Live delivery records

- **Status:** Accepted
- **Date:** 2026-10-18

## Context

[ADR 0020](0020-one-delivery-adapter-per-sending-platform.md) adds real sending, the seller's
Gmail first, and [ADR 0021](0021-the-sellers-gmail-credential.md) decides its credential. Three
things a real send needs are not recorded anywhere yet:

- **The recipient's address.** A draft names its recipient by `person_id`, and the prospect
  file gives only that ID and a first name. `DeliveryTarget` says recipients are records, not
  raw addresses, but no person record exists.
- **What happened to a send.** The only delivery event is `delivery.simulated`. ADR 0020 needs
  a send's receipt, a failure that sent nothing, and an unknown outcome that is never retried,
  told apart.
- **The scope's platform.** `ExecutionBoundary.delivery` says only whether delivery is
  disabled, simulated or live, not which platform a live scope sends from.

## Decision

- **People are records.** `research` stores the prospect's recipient as a `Person` record:
  `person_id`, first name and, for a scope that sends live, an email address. A person record
  is never edited. A new address means a new `person_id`, so an approval can never follow a
  changed address. `review` shows the address, and delivery sends only to the address of the
  person the approval names. Events that refer to a person refer to a stored one.
- **A send leaves a trail.** `delivery.attempted` is recorded before the request goes out.
  Then `delivery.sent`, with the platform's receipt, or `delivery.failed`, when the adapter knows
  nothing was sent, such as a refused credential. An attempt with neither is unknown. An
  unknown attempt blocks every later attempt to deliver that draft. The seller checks their Sent
  folder and records what they found with `auggie deliver --resolve sent` or
  `--resolve not-sent`. That closes the attempt, and the harness never retries the draft
  (ADR 0020). Only a failure the adapter itself recorded allows another attempt.
- **The scope names its platform.** `ExecutionBoundary` gains `platform`, which defaults to
  `simulated`. A live scope names a real platform, `gmail` first, and a simulated scope keeps
  `simulated`. `review` asks for delivery approval on the scope's platform, and delivery
  refuses an approval for any other.
- Simulated delivery keeps `delivery.simulated`, unchanged.

## Consequences

- The contracts gain a `Person` record, a `gmail` delivery platform, the three delivery events,
  a `person.recorded` event and `ExecutionBoundary.platform`. Each addition has a default or is
  new, so stored records still validate ([ADR 0012](0012-contract-conventions.md)).
- The Store gains person records, kept like every other record: immutable, in the scope's own
  database, and checked when an event refers to one.
- Stores written before this change hold no person records. Their drafts can no longer be
  delivered or have outcomes recorded: research again for a new draft.
- A prospect's email address is customer data. It lives in the scope's Store and the
  seller's own files, never in this repository
  ([ADR 0010](0010-public-repository-boundary.md)).
