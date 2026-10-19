# ADR 0009: Judgment and synthesis engines

- **Status:** Superseded by [ADR 0031](0031-a-model-writes-drafts.md)
- **Date:** 2026-10-17

## Question

What performs judgment (qualification) and synthesis (briefs and drafts), and in what
combination?

## Decision

- **Judgment uses deterministic rules** that the strategy file defines
  ([ADR 0013](0013-strategy-files.md)), behind the `Qualifier` interface.
- **Synthesis uses strategy play templates.** Their fixed wording is approved copy, and their
  fields are filled from admitted evidence and account records
  ([ADR 0014](0014-artifact-contract.md)).
- Jev and OpenRouter are not used yet. Either may be added behind the same interfaces only after
  it beats rules and templates on a labeled, synthetic evaluation set where they fall short.
  Adding one is a new ADR.

## Candidates

| Candidate | Considered for |
|---|---|
| Deterministic rules | Qualification criteria that can be checked mechanically |
| Jev | Calibrated evaluation; thresholds not yet set |
| OpenRouter | Generative synthesis through a choice of models |

## Constraints on every engine

- Judgment output is a `QualificationDecision`. Findings cite evidence, and `decided_by` records
  which rule set or model, and which version, produced it.
- Synthesis uses validated evidence only, and its output is reviewed by a person
  ([ADR 0005](0005-evidence-and-approval-gates.md)).
- Engines sit behind one interface, so they can be swapped or combined without changing the
  contracts.

## What would justify adding a model engine

- Agreement with human reviewers on a labeled, synthetic evaluation set.
- Reproducibility, which favors rules where they are enough.
- Cost and latency per account.
- Failure modes: how often each engine claims something the evidence does not support.
