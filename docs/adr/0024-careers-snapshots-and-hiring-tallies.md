# ADR 0024: Careers snapshots and hiring tallies

- **Status:** Proposed
- **Date:** 2026-10-18

## Context

A careers page read through Treg ([ADR 0017](0017-first-treg-route.md)) shows a few roles at
best. The signals a seller times outreach by are patterns across all of a company's open roles:
a new team, growth since last month, a run of senior hires. Seeing them needs the whole listing,
read again over time and compared.

Many companies publish their listing through a listing service that serves the open roles as
public JSON for its own careers pages. Reading that is one request, or a few paged requests,
costs nothing, and gives every role with its title, team, location, link and often its posting
date.

## Decision (proposed)

- **A careers snapshot is evidence:** what one company's listing showed on one date. A
  `CareersSnapshot` record holds:
  - the account and the listing, which is a kind, the company's name on that service and the
    public page a person opens;
  - when it was read, and whether it was retrieved or failed, with the reason;
  - whether it is complete;
  - every open role as the listing states it: title, team or department, location, link, and a
    posting date only when the listing gives one;
  - a content hash.

  Snapshots are never edited, and all of them are kept.
- **The listing is named, never guessed.** The prospect file names the account's listing, and
  the person confirms it. A listing guessed from the company's domain could be another company's.
- **Readers.** One reader per listing service, each a pure function from the service's reply
  to roles, tested on synthetic recorded replies. The first three are Lever, Greenhouse and
  Ashby, which publish job-board APIs for exactly this. Workday publishes none; its careers
  pages load their roles from an unpublished JSON endpoint, so that reader waits until its terms
  are checked.
  - Requests go only to that service's public API, through the shared transport, which allows
    no redirects.
  - They cost nothing, so they reserve no spend. They are still external calls, made only for a
    scope that allows external providers ([ADR 0016](0016-credentials-and-spend-limits.md)).
  - A listing longer than the role cap is stored as incomplete and reported as "at least N".
- **A failed read is recorded as failed**, with its reason. Criteria that depend on it are
  unknown, never "not hiring".
- **A hiring tally is a finding, not evidence.** Rules compute it from the newest retrieved
  snapshot and the previous one, using the strategy's vocabulary
  ([ADR 0023](0023-signal-vocabulary-in-the-strategy.md)). It gives open roles per family, the
  seniority mix, and the patterns that fire. Each pattern cites up to three role links and the
  snapshots it compared. A tally is never a score and never written by a model.
- **Judgment and drafts cite it.** A `hiring_family` or `hiring_pattern` finding cites the
  snapshots as its evidence. A play may quote a tally line, with its counts, its family's label
  and the snapshot's date, through a field.
- **When a snapshot is taken.** `research` takes one when the strategy has a hiring criterion
  and the prospect names a listing. `auggie hiring` takes one on demand. Nothing refreshes on a
  timer until the scheduler is chosen ([ADR 0007](0007-single-task-scheduler.md)).
- **The workbook gains a hiring tab:** account, snapshot date, listing, relevant and total
  roles, roles per family, and pattern lines with their links.
- **Data rules.** Public listings only, never LinkedIn's job pages, and no recruiter or contact
  names stored.

## Consequences

- New contracts, a Store collection in both backends, a `careers.recorded` event, and a
  `careers` record kind. A `QualificationDecision` finding may cite a snapshot as well as page
  evidence.
- Each listing service's terms of use, and the seller's company policy, apply to reading its
  listing, as with Treg.
- Listings with no public JSON need another reader later, or stay with the Treg page read.
- Not built.
