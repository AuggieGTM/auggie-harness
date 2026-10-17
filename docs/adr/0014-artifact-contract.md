# ADR 0014: One artifact contract for briefs and drafts

- **Status:** Accepted
- **Date:** 2026-10-17

## Context

Compose produces briefs and drafts. A `HumanApproval` binds to an `ArtifactRef` (ID, kind,
content hash), but no record held the content, what it was composed from, or how its versions
relate. Without that, a reviewer cannot see exactly what they approve, and an edit cannot be told
apart from a new artifact.

## Decision

- An `Artifact` record holds one version of a brief or draft:
  - `ref`: the `ArtifactRef` that approvals bind to,
  - the Markdown content, inline, which must hash to `ref.content_hash`,
  - the account, and for drafts the recipient (required for drafts, absent for briefs),
  - the qualification it follows and the evidence it uses (at least one record),
  - the `StrategyRef` it was composed under,
  - who composed it and when, and
  - `supersedes`: the content hash of the version it replaces, if any.
- An edit is a new version: the same `artifact_id`, a new hash, and `supersedes` naming the
  version it replaces. Versions form a single line. The Store refuses a version that does not
  supersede the current latest one.
- Only the latest version can be delivered. Approvals bind to one hash, so an edit voids every
  earlier approval of the artifact.
- The Store checks lineage when an artifact is written: the qualification exists, and cited
  evidence is retrieved, in scope and about the same account. Composers use only evidence that
  validation admitted.
- Composition is performed by a rule, a service or a model; an edit may be performed by a human.
  Which engines compose is still [ADR 0009](0009-judgment-and-synthesis-engines.md).

## Alternatives considered

- **Separate `Brief` and `Draft` contracts:** more exact per kind, but two contracts to keep in
  step, and briefs are not built yet.
- **`ArtifactRef` plus an opaque content blob, with lineage only in `draft.composed` events:**
  lighter, but nothing checks lineage when the artifact is written.

## Consequences

- Reviewers see the exact bytes they approve, and delivery re-checks the hash.
- Large or binary artifacts, such as PDF briefs, are not covered. They need content-addressed
  storage and a new ADR.
- Outcomes stay `outcome.recorded` events ([ADR 0012](0012-contract-conventions.md)).
