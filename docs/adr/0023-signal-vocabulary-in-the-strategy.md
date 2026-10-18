# ADR 0023: The seller's strategy holds the signal vocabulary

- **Status:** Proposed
- **Date:** 2026-10-18

## Context

The harness is for sellers of anything: a database, a security product, a customer data
platform, hardware. To work across many accounts, it needs to report which roles an account is
listing and which technologies its pages name, and what changed. The words for that differ by
seller. A security seller looks for detection engineers and Splunk; a CDP seller looks for
lifecycle marketers and Braze; a hardware seller looks for data-center engineers and VMware.

The strategy file ([ADR 0013](0013-strategy-files.md)) has three criterion rules today:
`headcount_range`, `job_posting` (title keywords on one page) and `mentions` (phrases on one
page). None of them can count roles by family, compare one reading with an earlier one, or ask
for a technology's name to appear near certain words.

## Decision (proposed)

**The vocabulary**

- **The code holds no domain vocabulary.** An optional `[signals]` section of the strategy
  supplies it, so a seller adapts the harness by editing a reviewed strategy revision, never
  code. The strategy is loaded only if every family and term a rule names is defined.
- **Hiring families.** `[signals.hiring.families.<id>]` gives a label and title phrases.
  Families are exclusive: a role belongs to the first family, in the file's order, with a phrase
  in its title. So the families' counts add up to the relevant count, and the rest is "other".
  Only titles are read.
- **Seniority.** Each title gets one level: the highest level, on a fixed ladder, whose words it
  contains. The ladder, from the top, is vice president and above, director, manager, lead,
  principal, staff, senior, mid, then junior. A title with no level words is *unlevelled*. The
  code ships generic words for each level. A strategy may replace a level's words but not the
  order.
- **Stack terms.** `[signals.stack.terms.<id>]` gives a label, the phrases that name the
  technology, and optional `near` words.
  - With `near`, a phrase counts only when the phrase and one of those words sit on one line of
    the page's text, with at most `near_words` words between them, on either side (default 8).
  - A word here is a run of letters and digits, so punctuation separates words.
  - A phrase checked for `near` words must lie within a single line.

**Matching**

- Matching is code, with no model.
  - Case is ignored, and a phrase's spaces match any run of whitespace.
  - A phrase matches only where no letter or digit is directly before or after it, so phrases
    such as `C++`, `.NET` and `go-to-market` work.
  - There is no stemming, so the same inputs give the same result.
- **Matches are heuristics, not facts about the account.** A title match shows only that the
  listing names a role with that title. A stack match shows only that a page names the
  technology near those words. "We do not use Splunk" matches too. So these findings are
  recorded as inference, with the matched words quoted, and the person judges them at review
  ([ADR 0005](0005-evidence-and-approval-gates.md)).

**Rules**

- **Listing patterns** are named for what they observe, and a strategy may give each a label of
  its own. They count open roles per family, and are defined in
  [ADR 0024](0024-careers-snapshots-and-hiring-tallies.md):
  - *new openings*: openings in a family that had none before;
  - *more openings*: more openings than before;
  - *senior openings*: openings that are all senior;
  - *leader opening*: a leader's opening in a small family;
  - *long listed*: a role listed for a long time.

  `[signals.hiring.patterns]` sets their thresholds.
- **New criterion rules** use the vocabulary:
  - `hiring_family`: at least `min_roles` open roles in a family;
  - `hiring_pattern`: a pattern holds for a family;
  - `stack_term`: a term is found in a named source.

  A failed, missing or incomplete reading leaves a criterion unknown unless ADR 0024 says that
  reading can settle it. A criterion is never unmet for lack of data.
- Adding an optional section and new rule kinds keeps every existing strategy file valid, and
  existing rules and template fields keep their meaning. So `schema_version` stays 1
  ([ADR 0012](0012-contract-conventions.md)). New template fields for tally lines are additive.

## Consequences

- The synthetic examples gain a second seller in another domain, and the tests run both. So
  nothing in the code can be specific to one domain, and neither example becomes a fallback
  vocabulary.
- Families and levels are not stored on snapshots. They are applied when roles are counted,
  under the strategy revision of the run that counts them. A later revision can read old
  snapshots again, but earlier decisions and drafts keep the revision they cite.
- A title the seller's phrases miss is counted as "other", and the tally shows that count. So a
  missing phrase is visible and is fixed in the strategy.
- The default seniority words misfire on some titles: "Senior Product Manager" reads as a
  manager. A seller whose market needs other words replaces them.
- Not built.
