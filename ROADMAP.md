# Roadmap

Milestones are an order of work, not dates. A milestone is done when its gate passes with
recorded evidence, not when its code is written. Status labels: **Done**, **In review**,
**In progress**, **Next**, **Planned**, **Later**.

## M0: Foundation (Done)

- README, AGENTS.md/CLAUDE.md, architecture, ADRs 0001–0012, this roadmap.
- Contracts: `CustomerScope`, `SourceEvidence`, `QualificationDecision`, `HumanApproval`,
  `OperationalEvent`.
- One synthetic scenario covering the first vertical slice, plus tests.
- CI (pytest, ruff, mypy) and a public-boundary check run on every push.

**Gate:** checks pass (pytest, ruff, mypy); maintainer accepts or amends ADR 0012; repository
reviewed for public-boundary compliance before the first push.

## M1: Store kernel (Done)

- A typed Store API for the M0 contracts, bound to one `CustomerScope`. *Exists.*
- An in-memory reference implementation and a MongoDB Community implementation (local
  container, one database per scope), both run against **one shared contract test suite**.
  *Both exist and pass the suite in `tests/store/`; CI runs it against MongoDB
  ([ADR 0015](docs/adr/0015-mongodb-store-layout.md)).*
- Append-only events with idempotency: appending the same key twice stores one fact. *Exists.*
- Secret scanning in CI. *gitleaks over full history in CI, and GitHub secret scanning with push
  protection.*
- Decide what, if anything, to reuse from existing private code. Nothing is copied until a
  licensing and confidentiality review is done. *Decided for M1: nothing reused.*

**Gate:** both implementations pass the same suite; cross-scope reads and writes are rejected;
the MongoDB implementation runs only through the Store API.

## M2: Synthetic vertical slice (Done)

Research → validate evidence → qualify → draft → human approval → simulated delivery → record
outcome, running through the Store with fixture adapters and no network access.

*Runs through the in-memory Store (`uv run auggie run examples/synthetic/northstar-msp`) and
through MongoDB (`--mongodb URI`), and the tests show the gate below on both.*

**Gate:** the Northstar scenario is produced by the workflow from its inputs, not prewritten.
Negative paths are blocked: failed source, edited draft after approval, delivery without
delivery approval, cross-scope record.

**Decisions:** strategy file format ([ADR 0013](docs/adr/0013-strategy-files.md)) and artifact
contract ([ADR 0014](docs/adr/0014-artifact-contract.md)), both Accepted.

## M3: First live routes (Next)

- One Treg acquisition route ([ADR 0008](docs/adr/0008-treg-for-acquisition.md)).
- A judgment engine chosen ([ADR 0009](docs/adr/0009-judgment-and-synthesis-engines.md)). *Decided:
  rules for judgment, templates for synthesis.*
- Read-only Sheets/Docs projection.
- Delivery stays simulated.

**Gate:** per-customer credentials; provider spend bounded per run; no live delivery.
**Decision needed first:** the task scheduler ([ADR 0007](docs/adr/0007-single-task-scheduler.md)),
if any work must run in the background. *M3 runs stay operator-triggered from the CLI, so ADR 0007
can stay open.*

## M4: Proven in real deployments (Planned)

Operators deploy the harness outside this repository, including the maintainer's own private
service. Their configuration, strategy and data stay in their own private repositories and
systems. Only generic fixes, and synthetic regression fixtures that reproduce what was learned,
land here.

**Gate:** two independent deployments differ by configuration only.

## M5: Delivery and learning (Later)

- EmailBison/Instantly delivery adapter behind delivery approval.
- Outcome intake and strategy-revision proposals that people review in Git.

## Public release readiness (Later)

`SECURITY.md`, `CONTRIBUTING.md`, third-party notices, and installation verified on fresh
machines.
