# ADR 0023: The seller's strategy holds the signal vocabulary

- **Status:** Proposed
- **Date:** 2026-10-18

## Context

The harness is for sellers of anything: a database, a security product, a customer data
platform, hardware. To work across many accounts, it needs to say who is hiring for what, which
technologies an account runs, and what changed. The words for that differ by seller. A security
seller looks for detection engineers and Splunk; a CDP seller looks for lifecycle marketers and
Braze; a hardware seller looks for data-center engineers and VMware.

The strategy file ([ADR 0013](0013-strategy-files.md)) has three criterion rules today:
`headcount_range`, `job_posting` (title keywords on one page) and `mentions` (phrases on one
page). None of them can count roles by family, compare one reading with the last, or tell a
technology named in passing from one an account runs.

## Decision (proposed)

- **The code holds no domain vocabulary.** An optional `[signals]` section of the strategy
  supplies it, so a seller adapts the harness by editing a reviewed strategy revision, never
  code.
- **Hiring families.** `[signals.hiring.families.<id>]` gives a label and title phrases. A role
  belongs to a family when its title contains one of the phrases as whole words, ignoring case.
  Only the title is read. A role in no family counts only towards the total.
- **Seniority.** The code has a default ladder of job-title levels (junior, mid, senior, staff,
  principal, lead, manager, director, vice president and above). It names levels, not domains,
  and a strategy may replace its words.
- **Hiring patterns.** `[signals.hiring.patterns]` sets the thresholds, with these defaults:
  - *new team*: at least 2 open roles in a family that had none in the previous snapshot;
  - *growth*: at least 3 more open roles than a snapshot at least 28 days older;
  - *senior-heavy*: at least 3 senior-or-above roles in a family and no junior or mid ones;
  - *leadership first*: a manager-or-above role in a family with at most one other open role;
  - *still hiring*: the oldest dated role listed at least 120 days ago, only when the listing
    states posting dates.
- **Stack terms.** `[signals.stack.terms.<id>]` gives a label, the phrases that name the
  technology, and optional `near` words. A term counts where a page names it, and, when `near`
  is given, only within a few words of one of them, so a name in a customer list or a menu does
  not count. Each match cites its evidence and the words it matched.
- **New criterion rules** use the vocabulary:
  - `hiring_family`: met when the newest careers snapshot has at least `min_roles` open roles in
    a family;
  - `hiring_pattern`: met when a pattern fires for a family;
  - `stack_term`: met when a term is found in a named source.

  As with the existing rules, a failed or missing read leaves the criterion unknown, never
  unmet.
- **Matching is code.** It uses whole words with case ignored, no stemming, and no model, so the
  same inputs always give the same result. Plays select on these criteria as they do today.
- Adding an optional section and new rule kinds keeps every existing strategy file valid, so
  `schema_version` stays 1 ([ADR 0012](0012-contract-conventions.md)).

## Consequences

- The synthetic examples gain a second seller in another domain, and the tests run both, so
  nothing in the code can be specific to one.
- Families are not stored on snapshots. They are applied when roles are counted, so a revised
  vocabulary reads every earlier snapshot the same way.
- A title the seller's phrases miss is counted as "other". The tally shows that count, so a
  missing phrase is visible and is fixed in the strategy.
- Careers snapshots, which the hiring rules read, are
  [ADR 0024](0024-careers-snapshots-and-hiring-tallies.md).
- Not built.
