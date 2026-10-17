# ADR 0009: Judgment and synthesis engines

- **Status:** Open
- **Date:** 2026-10-17

## Question

What performs judgment (qualification) and synthesis (briefs and drafts), and in what
combination?

## Candidates under evaluation

| Candidate | Considered for |
|---|---|
| Deterministic rules | Qualification criteria that can be checked mechanically |
| Jev | Calibrated evaluation; thresholds not yet set |
| OpenRouter | Generative synthesis through a choice of models |

## Already decided

Whatever is chosen:

- Judgment output is a `QualificationDecision`. Findings cite evidence, and `decided_by` records
  which rule set or model, and which version, produced it.
- Synthesis uses validated evidence only, and its output is reviewed by a person
  ([ADR 0005](0005-evidence-and-approval-gates.md)).
- Engines sit behind one interface, so they can be swapped or combined without changing the
  contracts.

## What would decide it

- Agreement with human reviewers on a labeled, synthetic evaluation set.
- Reproducibility, which favors rules where they are enough.
- Cost and latency per account.
- Failure modes: how often each engine claims something the evidence does not support.
