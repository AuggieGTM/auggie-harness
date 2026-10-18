# ADR 0028: Learning from outcomes: a report, and proposals as a patch

- **Status:** Accepted
- **Date:** 2026-10-18

## Context

A person records what happened after each delivery with `auggie outcome`: a positive or negative
reply, a meeting booked, an opt-out, a bounce, or no reply. Nothing reads those outcomes back.
A seller working many accounts wants to know which plays, pains, proofs and signals lead to
replies, and to change the strategy accordingly. [ADR 0005](0005-evidence-and-approval-gates.md)
already decides that learning proposes and people decide: a change takes effect only as a
reviewed Git revision, and only for later runs. The strategy file is read, never written
([ADR 0013](0013-strategy-files.md)).

## Decision

The maintainer chose a report with proposals as a patch, proposals that only reorder, outcomes
taken one at a time or from a CSV, and a threshold of 10.

**What a delivery's result is**

- Each delivered draft counts once, whether it was sent or handed off in simulation.
- Its result comes from every outcome reported for it:
  - *positive* if any report is a positive reply or a meeting booked;
  - *bounced* if one is a bounce and none is positive; a bounce is not a delivery that was read,
    so it counts toward neither rate;
  - *negative* if the reports are only negative replies, opt-outs or no reply;
  - *waiting* while nothing is reported.
- A delivery is *settled* when it is positive or negative. A rate is positive deliveries over
  settled ones, so deliveries still waiting never lower it.

**The report**

- `auggie learn <scope.json>` reports, from the Store alone, per play, pain, proof and met
  criterion: deliveries, positive, negative, opt-outs, bounced and waiting, and the positive
  rate. With `--json` it prints the same as JSON for a coding agent. It reads and writes
  nothing else.
- Drafts record the play they were composed from, so every draft can be counted by play. A
  draft from before that has no play and is counted as such.

**Proposals**

- `auggie learn ... --propose <file>` also writes proposed changes to the strategy as a patch:
  a unified diff against the strategy file at the given revision, which `git apply` takes in
  the strategy repository. The person reviews it, applies it and commits it, or throws it away.
  The harness never applies a patch and never writes the strategy repository.
- **Order only.** A proposal may reorder the plays, which changes which play a qualified account
  gets first, and the proofs of each pain, best first. It never writes new wording and never
  removes anything.
- **Only with enough results.** A play or proof is reordered only when it has at least 10
  settled deliveries (`--min-settled` may raise it). Higher positive rates come first, ties keep
  their order, and anything with fewer settled deliveries keeps its place.
- **Checked before it is written.** The proposed file must parse, and differ from the current
  strategy only in that order. A change that cannot be made that way is listed for the person
  to make by hand instead. The patch file is new, readable by its owner only, and never
  replaces a file.

**Taking outcomes in**

- `auggie outcome` records one outcome for one draft, as now.
- `auggie outcome <scope.json> --from <outcomes.csv> --reporter <you>` reads a CSV with the
  columns `draft`, `outcome` and `occurred_at`, and previews each row: recorded, already
  recorded, or why it cannot be (an unknown or undelivered draft, an unknown outcome, a bad
  time). With `--write` it records the rows it can.

## Consequences

- `Artifact` gains an optional `play`; earlier drafts load unchanged.
- No model reads outcomes or writes proposals ([ADR 0009](0009-judgment-and-synthesis-engines.md)).
- Reading replies from the seller's mailbox needs a read permission beyond the send-only Gmail
  token ([ADR 0021](0021-the-sellers-gmail-credential.md)); it is a later decision.
- Not built.
