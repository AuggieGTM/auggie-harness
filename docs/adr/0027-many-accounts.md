# ADR 0027: Many accounts: importing a list, researching it, reviewing its drafts

- **Status:** Accepted
- **Date:** 2026-10-18

## Context

A seller works a list of accounts, not one. Today each prospect is a JSON file the seller writes
by hand, `research` takes one of them, and `review` takes one draft by its ID. M6 needs three
things: a list brought in from the CSV a seller already has, the whole list researched in one
foreground run within a spending cap, and a way to step through the drafts waiting for a person.
Each draft is still approved on its own ([ADR 0005](0005-evidence-and-approval-gates.md)).

## Decision

The maintainer chose prospect files as the import's output, one spending cap for a whole list,
skipping accounts already researched, and stopping a list on an error.

**Importing a list**

- `auggie import <scope.json> <list.csv> --prospects <folder>` reads the CSV and shows a
  preview: for each row, the account it becomes or why it cannot be one. It writes nothing.
  With `--write`, it shows the same preview and then writes one prospect file per valid row
  into the folder, which should sit outside the clone.
- **Prospects stay files.** They remain inputs, as today: the Store does not hold accounts.
- **Columns.** The first row names the columns:
  - `name` (required): the company;
  - `key` (optional): the short handle in record IDs, derived from the name when absent; the
    account's ID is `acc-<key>`;
  - `first_name` (required), `person_id` and `email`: the recipient; `person_id` defaults to
    `per-<key>`;
  - one column per source kind the strategy names, holding that page's URL;
  - `listing_service`, `listing_board` and `listing_url`: the careers listing, all three or
    none ([ADR 0024](0024-careers-snapshots-and-hiring-tallies.md)).

  A column that is none of these makes the whole file refused, since it is probably a typo.
  The import takes the strategy, so it knows the source kinds.
- **Rows.** Each row is checked as a prospect file is. A row that fails is shown with its
  problems and not written. A key used twice is refused the second time. An existing file for
  the same account is left alone: if it is identical, nothing changes; if it differs, the row is
  shown as a conflict and not written. A list holds at most 1,000 rows.

**Researching a list**

- `auggie research-list <scope.json> --prospects <folder>` researches every prospect in the
  folder, in file-name order, in one foreground run. No scheduler or background worker runs it
  ([ADR 0007](0007-single-task-scheduler.md)).
- **One cap for the list.** The whole list shares one run limit: the scope's per-run limit, or
  a lower `--limit` ([ADR 0016](0016-credentials-and-spend-limits.md)). An account starts only
  when its worst case, one Treg page ceiling for each source it names, fits what is left. The
  first account that does not fit stops the list, and it and the rest wait for the next run.
  Listing reads are free and need no room.
- **Each account is its own research.** It gets its own run ID, records and qualification, as a
  single `research` makes.
- **Accounts already researched are skipped.** An account with a qualification in the Store is
  skipped unless `--again` is given. So an interrupted list resumes without paying twice.
- **An error stops the list.** A failed page read is recorded as usual and the list goes on.
  An error is a refusal or a bug, so the list stops there and reports which accounts were
  researched, which were skipped and which are waiting.

**Reviewing the drafts**

- `auggie review <scope.json> --next --reviewer <you>` shows the oldest draft waiting for a
  person: its latest version has no content decision, and it has not been delivered or ruled
  out. After each review it asks whether to go on to the next.
- It runs only at a terminal, like `review`. Each draft is reviewed and approved on its own;
  nothing approves a batch.

## Consequences

- The research shell keeps one-account `research`. Researching a list from the shell is a later
  decision.
- A list's run spends at most its cap, and its report says what each account spent.
- M6's gate can be tested: a list of accounts is researched in one run within its cap, and each
  draft is still approved on its own.
- Not built.
