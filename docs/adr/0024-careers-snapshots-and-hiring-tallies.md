# ADR 0024: Careers snapshots and hiring tallies

- **Status:** Proposed
- **Date:** 2026-10-18
- **Would supersede:** [ADR 0008](0008-treg-for-acquisition.md)

## Context

A careers page read through Treg ([ADR 0017](0017-first-treg-route.md)) shows a few roles at
best. The listing patterns a seller times outreach by
([ADR 0023](0023-signal-vocabulary-in-the-strategy.md)) need every open role, read again over
time and compared.

Many companies publish their listing through a listing service. Lever, Greenhouse and Ashby
document public job-board APIs that serve every open role as JSON, for companies' own careers
pages. Reading one is one request, or a few paged ones, costs nothing, and gives each role's
title, team, location and link, and often a date.

[ADR 0008](0008-treg-for-acquisition.md) says that Treg handles data acquisition. Reading these
feeds directly is an exception to that, so this ADR would supersede 0008 by restating it with
the exception.

## Decision (proposed)

**Acquisition (restating ADR 0008, with one exception)**

- Treg handles data acquisition, through an adapter. What it returns is recorded as
  `SourceEvidence`, and its credentials resolve per scope. Each route is enabled on its own,
  with tests, and Treg's details stay inside its adapter.
- **The exception: listing services' public job-board feeds** are read directly. Each reader is
  enabled on its own, with tests, and its details stay inside it.

**Snapshots**

- **A careers snapshot is evidence:** what one company's listing showed when one read
  attempt was made. A `CareersSnapshot` record holds:
  - the account;
  - the listing: the service, the company's name on it, and the public page a person opens;
  - when it was read, and whether the read was retrieved or failed, with the reason;
  - whether it is complete;
  - every open role as the listing states it: the service's own role ID, title, team or
    department, location, link, and a date when the listing gives one;
  - a content hash.

  Snapshots are never edited, and all of them are kept, failed ones included.
- **The listing is named, never guessed.** The prospect file names the account's listing, and
  the person confirms it. The harness never picks another listing when one fails.
- **Readers.** One reader per service, starting with Lever, Greenhouse and Ashby.
  - Each is a pure function from the service's replies to roles, tested on synthetic recorded
    replies.
  - Each cites the service's documentation for its endpoint, paging, role identity and date
    field.
  - Each reads the one date field the documentation defines as when the role was posted, never
    an update or republication time.
  - Requests go only to that service's public API, through the shared transport, which allows
    no redirects.
  - Readers cost nothing, so they reserve no spend. They are still external calls, made only
    for a scope that allows external providers ([ADR 0016](0016-credentials-and-spend-limits.md)).
  - Workday's careers sites have no documented public job-board feed; their pages load roles
    from an undocumented endpoint. That reader waits until its terms are checked.
- **Complete, incomplete or failed.**
  - A read is *complete* only when the reader has read every page, and the service's own paging
    or totals confirm it.
  - A read that stops at the role cap, or whose pages disagree with each other or with the
    stated total, is *incomplete*. It is reported as "at least N".
  - A reply that is malformed, has no roles array or cannot be read is *failed*, never an empty
    listing.
  - Roles are counted once each, by the service's role ID.
  - A Treg page read never becomes a snapshot.

**Tallies**

- **A hiring tally is a finding, not evidence.** Rules compute it with the strategy's vocabulary,
  under the strategy revision of the run, and record it as inference:
  - open roles per family, and the seniority mix;
  - the listing patterns that hold.

  It cites the snapshots it used, and up to three role links to illustrate each pattern. It is
  never a score, and never written by a model.
- **The current reading is the run's own.** Each `research` makes one read attempt per
  listing, and the run's findings come from that attempt only. If it fails, every criterion that
  depends on it is unknown. An older snapshot is never used in its place.
- **The baseline**, used by *new openings* and *more openings*, is the newest *complete*,
  retrieved snapshot of the same account and listing that was read at least `baseline_days`
  before the current one (default 28) and no more than `max_lookback_days` before it
  (default 180). Ties go to the later record. With no such snapshot, both patterns are unknown.
- **What each reading can settle.** "Met" may rest on an incomplete current reading where a
  lower bound is enough. "Unmet" needs complete readings.

  | Criterion or pattern (per family) | Met when | Unmet when | Otherwise |
  |---|---|---|---|
  | `hiring_family` | current has at least `min_roles` roles, complete or not | current is complete and has fewer | unknown |
  | *new openings* | baseline has none and current has at least `min_roles` (default 2) | baseline has none and current is complete with fewer, or baseline has some | unknown |
  | *more openings* | current, complete or not, has at least `min_increase` (default 3) more than baseline | current is complete and the increase is smaller | unknown |
  | *senior openings* | current is complete, at least `min_roles` (default 3) roles are senior or above, and none are junior, mid or unlevelled | current is complete and that does not hold | unknown |
  | *leader opening* | current is complete, a role is manager or above, and the family has at most `max_others` (default 1) other roles | current is complete and that does not hold | unknown |
  | *long listed* | a role's posting date is at least `min_days` (default 120) before the current read | current is complete, every role in the family is dated, and all are younger | unknown |

  "Before" is always measured against the snapshot's read time, never today's date.
- **Judgment and drafts cite it.** A `hiring_family` or `hiring_pattern` finding cites its
  snapshots as evidence, together with its rationale: the counts, the family's label and the
  read date. A play may quote that rationale through a field. It is shown as what the listing
  showed on a date, never as a fact about the account.
- **When a snapshot is taken.** `research` takes one when the strategy has a hiring criterion
  and the prospect names a listing, and `auggie hiring` takes one on demand. Nothing refreshes
  on a timer until the scheduler is chosen ([ADR 0007](0007-single-task-scheduler.md)).
- **The workbook gains a hiring tab.** It shows the account, the read date, the listing,
  relevant and total roles, roles per family, and the pattern lines with their links.
- **Data rules.** Public listings only, never LinkedIn's job pages, and no recruiter or contact
  names stored.

## Consequences

- On acceptance, ADR 0008 is marked superseded. Treg's routes, spend checks
  ([ADR 0016](0016-credentials-and-spend-limits.md)) and retention terms
  ([ADR 0017](0017-first-treg-route.md)) are unchanged.
- A failed listing read never falls back to a paid Treg page read.
- Before a reader runs live, the person checks that service's terms of use and retention, and
  their company's policy, as with Treg. Public availability alone does not grant a right to keep
  the data.
- New contracts, a Store collection in both backends, a `careers.recorded` event and a
  `careers` record kind.
- A `QualificationDecision` finding can cite snapshots as well as page evidence. Those
  citations are checked like evidence citations: the same scope and account, and a snapshot
  that was read in this run or is its baseline.
- Listings with no public feed need another reader later, or keep only the Treg page read.
- Not built.
