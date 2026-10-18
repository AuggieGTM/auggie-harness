# ADR 0026: The seller's point of view: pains, proofs and offers

- **Status:** Accepted
- **Date:** 2026-10-18

## Context

The harness can now say which accounts are hiring in the seller's families and which pages name
a technology ([ADR 0023](0023-signal-vocabulary-in-the-strategy.md),
[ADR 0024](0024-careers-snapshots-and-hiring-tallies.md)). A draft still says nothing about why
the seller is worth a reply. A seller answers that with a point of view: a problem teams like
the account's often have, where the seller fits and where it stops, a customer who saw a result,
and one clear ask.

The seller's case studies hold the proof, and they are the seller's own marketing. So a proof
is a vendor claim ([ADR 0005](0005-evidence-and-approval-gates.md)). It must be quoted exactly
from one page that says it, and never restated as a fact. Judgment stays rules and synthesis
stays templates ([ADR 0009](0009-judgment-and-synthesis-engines.md)): no model finds, writes or
picks a proof.

## Decision

The maintainer chose to check each proof against a read of its page, to give a pain a core of
approved copy with an optional metric, and to let a draft quote a hiring tally's count, family
and date.

**In the strategy**

The strategy file ([ADR 0013](0013-strategy-files.md)) gains two optional sections. Both are
approved copy, reviewed in Git like the rest of the strategy.

- **Proofs.** `[proofs.<id>]` names one customer story:
  - `customer`: the customer's name, as the page gives it;
  - `url`: the one public page that says it;
  - `claim`: the exact sentence the draft may quote, copied from that page;
  - `kind`: `result` (something changed) or `scale` (what the customer runs);
  - `caveat`, optional: what the story does not prove, shown at review only.
- **Pains.** `[pains.<id>]` names one angle:
  - `statement`: the problem, written as one teams like the account's often have. It is an
    angle, never a fact about the account, and nothing records it as a finding;
  - `fit`: where the seller fits;
  - `limits`: where the seller stops;
  - `offer`: the one ask a draft closes on;
  - `proofs`: proof IDs, best first;
  - `metric`, `threshold` and `consequence`, all optional: what to measure, where it starts to
    hurt, and what breaks then. Public pages rarely show an account's numbers, so these are
    shown at review only and never in a draft.
- **Plays.** A play may name one `pain`. Its template may then use `{pain}`, `{offer}`,
  `{proof_claim}` and `{proof_customer}`, filled from that pain's approved copy and its first
  usable proof. A play may also name one `hiring` criterion it requires, of rule
  `hiring_family`, and use:
  - `{hiring_count}`: the role count from this run's tally, as "at least 2" when the read was
    partial;
  - `{hiring_family}`: the family's label;
  - `{hiring_date}`: the read date, written as 15 October 2026.

  These fields never carry role titles or links. Every other word of a template stays approved
  copy.
- **Selection.** A play applies when every criterion it requires is met and, if its template
  uses a proof field, its pain has a usable proof. The first play that applies is used, as now.
  So a seller can list a play without a proof after one with a proof, as a fallback.

**Checking proofs**

- **Each proof's page is read and recorded.** `auggie proofs` lists every proof in the strategy
  and whether it is usable. `auggie proofs --read` reads each proof's page through Treg. It is a
  paid call under the scope's run limit ([ADR 0016](0016-credentials-and-spend-limits.md)), and
  the shell's `proofs --read` asks first ([ADR 0025](0025-the-research-shell.md)).
- **A read is evidence about the seller.** It is recorded as `SourceEvidence` whose subject is
  the seller, a new subject kind whose ID is the scope's customer, and it is validated like any
  page.
- **A proof is usable only when:**
  - the newest read of its page was retrieved and admitted;
  - that read is no more than `max_age_days` old (set under `[proofs]`, default 180);
  - the read's stored text holds the claim word for word, with case kept and any run of
    whitespace matching any other.

  A newer read that failed makes the proof unusable. An older read never stands in for it, as
  for listings in ADR 0024.
- **Research reads no proof pages.** It uses the reads already recorded, so a run costs the same
  with or without proofs. A seller runs `proofs --read` after changing the strategy, and again
  when reads grow old.

**Drafts and review**

- A draft that uses a proof cites the proof's read in its evidence, beside the account's own
  evidence and snapshots ([ADR 0014](0014-artifact-contract.md)).
- `review` shows each proof as a vendor claim: the claim, the customer, the page and when it was
  read, with its caveat. It also shows the pain's fit and limits and, when given, its metric,
  threshold and consequence. The person approves the exact draft, as always.

**Out of scope**

- Finding case studies and choosing their claims. The seller does that, or their coding agent
  proposes it as a strategy change the seller reviews.
- Gated content, PDFs and video. Only public pages Treg reads.
- Telling which pain an account has. A pain is chosen by the play, which is chosen by the
  signals the play requires.

## Consequences

- The `Strategy` contract gains optional `proofs` and `pains`, and plays gain optional `pain`
  and `hiring`. Every existing strategy file stays valid, so `schema_version` stays 1
  ([ADR 0012](0012-contract-conventions.md)).
- `SubjectRef` gains the seller kind. The Store's citation check accepts evidence about the
  seller of the same scope as well as about the account.
- A Treg read keeps the first 4,000 characters of a page's text
  ([ADR 0017](0017-first-treg-route.md)). A claim that comes later cannot be checked, so its
  proof is unusable. Choosing a smaller page or raising the limit is a later decision.
- The second synthetic seller gains a pain and a proof, and its scenario drafts from both.
- M5's gate can be tested: every proof in a draft is a vendor claim with exactly one source,
  quoted exactly, and a person still approves each draft.
- Not built.
