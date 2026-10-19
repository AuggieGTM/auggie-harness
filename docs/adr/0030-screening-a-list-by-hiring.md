# ADR 0030: Screening a list by hiring before paying to research it

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

`auggie research-list` researches every prospect in a folder in one run, within one cap
([ADR 0027](0027-many-accounts.md)), which ADR 0027 left out of the research shell
([ADR 0025](0025-the-research-shell.md)) for a later decision. Research reads each account's
pages through Treg, which is paid, while reading a careers listing is free
([ADR 0024](0024-careers-snapshots-and-hiring-tallies.md)). A seller whose strategy requires
hiring in their families can therefore learn which accounts qualify on hiring for free, and pay
only for those.

## Decision

The maintainer chose to screen by hiring first, to ask once per batch, to leave out accounts
the screen cannot judge, and to offer review afterwards.

**Screening**

- The shell's `research --hiring`, and `auggie research-list --screen`, research a folder of
  prospects in two steps.
- **First, a free screen.** For each prospect not yet researched (unless `--again`) that names a
  careers listing, the listing is read once and recorded as a snapshot, as `hiring --read`
  does. The strategy's required hiring criteria are judged on that read by the rules' own tally,
  with the account's earlier snapshots as baselines. An account passes when every one of them
  is met.
- **Then, paid research of the accounts that passed**, as `research-list` researches them, under
  one cap for the batch. Research makes its own read of each listing, as ADR 0024 requires;
  the screen's read is a separate snapshot.
- **Accounts the screen cannot judge are left out**: no listing, a read that failed or was
  inconsistent, or a required criterion that stays unknown. They are listed as not screened,
  with why, for the person to research by hand. An account shown not to be hiring is listed as
  screened out.
- A strategy that requires no hiring criterion has nothing to screen by, and the screen is
  refused.

**Asking and reviewing**

- When reads are live, the shell asks once before the listing reads, saying how many and that
  they are free, and once before the paid research, saying how many accounts, the most they
  can cost and the cap. Declining either reads or spends nothing further. With recorded
  responses nothing is called, so nothing is asked.
- When the batch leaves drafts waiting for a person, the shell offers to review them one by one
  (`review --next`, ADR 0027), each approved on its own.

## Consequences

- A screened list pays only for the accounts hiring as the strategy requires, plus nothing for
  the listings.
- `research-list` without `--screen` and the shell's one-account `research` are unchanged.
- Screening reads each passing listing twice in one batch: once to screen, once in research.
  Both are free.
- Not built.
