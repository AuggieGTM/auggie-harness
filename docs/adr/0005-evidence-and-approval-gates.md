# ADR 0005: Evidence before judgment; human approval before delivery

- **Status:** Accepted
- **Date:** 2026-10-17

## Context

The harness aims for reliable account intelligence and thoughtful outreach, not autonomous mass
outreach. Generated text can mix facts, guesses and errors, and anything sent reaches a real
person.

## Decision

- **Evidence is recorded as observed.** `SourceEvidence` records what a source said, with dates,
  provenance and a content hash. Only services and humans acquire evidence; a model's reading of
  a source is a finding, not evidence.
- **Gaps stay visible.** A failed retrieval is recorded with its reason. It never becomes a
  negative finding.
- **Judgment cites.** A `QualificationDecision` names the strategy revision and classifies each
  finding as source fact, vendor claim, inference or hypothesis. Settled criteria must cite usable
  evidence about the same account. A hypothesis cannot settle a criterion.
- **Composition uses validated evidence only.**
- **People approve exact artifacts.** A `HumanApproval` binds to an artifact's content hash and a
  scope. Any edit voids it. Approving content does not authorize delivery; a delivery approval
  names its target. Reviewers must be human, so nothing approves itself.
- **Learning proposes; people decide.** Outcomes can suggest strategy changes. A change takes
  effect only as a reviewed Git revision, and only for later runs.

## Consequences

- Every sent message can be traced to an approval, a draft hash, a qualification and its
  evidence.
- Throughput is limited by review capacity. That is intended.
- An interactive "yes" proves only that someone with access answered. These controls separate
  routine roles; they do not protect against a fully privileged host operator.
