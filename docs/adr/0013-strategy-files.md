# ADR 0013: Strategy files are TOML, one file per revision

- **Status:** Accepted
- **Date:** 2026-10-17

## Context

[ADR 0003](0003-systems-of-record.md) keeps approved strategy in Git, and every record that
depends on it cites the exact revision with a `StrategyRef` (commit, path, digest). It left the
file format open. The first synthetic slice needs a strategy file to qualify an account and
compose a draft. People review strategy changes in pull requests, so the format must be easy to
read and diff, allow comments, and load without ambiguity.

## Decision

- A strategy is a TOML file in the customer's knowledge namespace, read with Python's standard
  `tomllib`. This adds no dependency.
- The file is validated by the `Strategy` contract under the conventions of
  [ADR 0012](0012-contract-conventions.md): a `schema_version`, unknown keys rejected, and a
  closed set of rule kinds. Its `customer_id` must match the scope it is loaded for.
- One file holds what a run needs: qualification criteria with their rule parameters, which
  criteria are required, and plays (draft templates and the criteria each one needs).
- A `StrategyRef` names the Git commit the file was read at, its path in the knowledge
  repository, and the sha256 digest of its exact bytes. Any byte change, including a comment,
  is a new revision.
- Templates are TOML multi-line literal strings with `{field}` placeholders. Rendering fills the
  placeholders and never reflows text, so the same inputs give the same bytes.
- The harness reads strategy and never writes it. Changes are reviewed Git revisions
  ([ADR 0005](0005-evidence-and-approval-gates.md)).
- Synthetic fixtures have no strategy repository, so they declare a placeholder commit. Their
  digest is still computed from the file.

## Alternatives considered

- **YAML:** familiar to operators, but needs a dependency and has implicit typing (unquoted `no`
  becomes false; unquoted dates become dates).
- **JSON:** strict and dependency-free, but has no comments and is harder to review.

## Consequences

- A new rule kind is a code change to the rules and the `Strategy` contract.
- `tomllib` only reads. Anything that proposes a strategy change produces a text diff for review.
- The fixed wording of a play template is approved copy. Only its placeholders are filled from
  evidence and input records, and a placeholder that cannot be filled blocks the draft.
