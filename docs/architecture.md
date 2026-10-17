# Architecture

> **Status: early, synthetic only.** The contracts, the Store (in-memory and MongoDB) and the
> first vertical slice exist and run on synthetic fixtures. Every component below is labeled
> **Exists**, **Proposed** (recommended, not yet accepted or built) or **Open** (unresolved).
> Decisions are recorded in [`docs/adr/`](adr/README.md).

## Purpose

Auggie is a GTM-as-code harness. Approved strategy is versioned in Git. The harness turns it into
an evidence-to-action-to-learning loop for one customer at a time. It aims to produce:

- account intelligence that can be traced to dated sources,
- outreach a person has read and approved, and
- institutional knowledge that accumulates across runs and strategy revisions.

**Non-goals:** autonomous mass outreach, a hosted multi-tenant SaaS product, SMTP or sequencing
infrastructure, a CRM replacement, or a second task scheduler.

## The loop

```mermaid
flowchart LR
  D[Define] --> O[Observe] --> V[Validate] --> J[Judge] --> C[Compose] --> A[Approve] --> X[Deliver] --> L[Learn]
  L -. proposes a new strategy revision .-> D
```

| Stage | Responsibility | Contract / events | Status |
|---|---|---|---|
| Define | Approved strategy and configuration, versioned in Git | `Strategy`, `StrategyRef`, `strategy.activated` | TOML files load ([ADR 0013](adr/0013-strategy-files.md)) |
| Observe | Acquire sources about an account; record every attempt | `SourceEvidence`, `evidence.recorded` | Fixture replay; Treg page text, not yet run live |
| Validate | Admit usable evidence and reject the rest, keeping reasons | `evidence.validated`, `evidence.rejected` | Rules exist |
| Judge | Qualify against an exact strategy revision, citing evidence | `QualificationDecision`, `qualification.decided` | Deterministic rules; engines open |
| Compose | Write briefs and drafts from validated evidence only | `Artifact`, `draft.composed` | Draft templates ([ADR 0014](adr/0014-artifact-contract.md)); briefs not built |
| Approve | A person approves one artifact version for one scope | `HumanApproval`, `approval.recorded` | Recorded from scripted decisions; no review UI |
| Deliver | Hand approved drafts to an existing sending platform | `DeliveryTarget`, `delivery.simulated` | Authorization gate; simulated only |
| Learn | Record outcomes; propose strategy changes for human review | `outcome.recorded` | Event exists; learning proposed |

Learning never rewrites active strategy. A proposed change becomes a reviewed Git change, and only
later runs use it ([ADR 0005](adr/0005-evidence-and-approval-gates.md)).

## Where truth lives

| System | Holds | Authority | ADR |
|---|---|---|---|
| GitHub | Approved strategy and executable configuration | Authoritative for what is approved | [0003](adr/0003-systems-of-record.md) |
| MongoDB Community, one database per customer, behind a typed Store API | Evidence, decisions, approvals, events, outcomes | Operational system of record | [0003](adr/0003-systems-of-record.md), [0015](adr/0015-mongodb-store-layout.md) |
| Google Sheets / Docs | Customer-facing reports and review views | None: projections of the Store | [0003](adr/0003-systems-of-record.md) |
| EmailBison / Instantly | Sending | Delivery receipts only | [0006](adr/0006-delivery-through-existing-platforms.md) |
| SageOx | Shared engineering context and rationale | None over execution; never customer intelligence | [0011](adr/0011-sageox-engineering-context.md) |

Every operational record that depends on strategy carries a `StrategyRef`: Git commit, path and
content digest. Any result can be traced to the exact approved revision that produced it.

## Customer isolation

Each customer has a `CustomerScope` with isolated credentials, data, knowledge and execution
([ADR 0004](adr/0004-customer-isolation.md)):

```mermaid
flowchart TB
  subgraph A["CustomerScope: customer-a"]
    A1[credential namespace] --- A2[(data namespace: own MongoDB database)]
    A2 --- A3[knowledge namespace: own strategy]
    A3 --- A4[execution boundary: delivery mode, external providers]
  end
  subgraph B["CustomerScope: customer-b"]
    B1[credential namespace] --- B2[(data namespace: own MongoDB database)]
    B2 --- B3[knowledge namespace: own strategy]
    B3 --- B4[execution boundary]
  end
```

- Every customer-bound record carries `customer_id`. An operation bound to one scope rejects
  records from another (`ensure_in_scope`).
- No two scopes may share a customer ID or any namespace (`check_isolated`).
- A synthetic scope cannot enable live delivery.
- Distinct names do not prove isolation. Deployments must also enforce it with separate
  credentials, database users and processes. The contracts catch misconfiguration; they are not
  a security boundary on their own.
- Customer deployments differ by configuration, not by code forks.

## Components

```mermaid
flowchart LR
  G[(GitHub: approved strategy)] --> S
  T[Treg acquisition adapter] --> S
  J[Judgment: rules; synthesis: templates] --> S
  S[Typed Store API] --> M[(MongoDB Community, per customer)]
  S --> P[Projections: Sheets / Docs]
  S --> DA[Delivery adapter] --> E[EmailBison / Instantly]
  R{{One task scheduler: Herdr or Foreman}} -.-> S
```

| Component | Role | Status |
|---|---|---|
| Contracts | Shared, versioned data shapes and invariants | **Exists** |
| Store API | The only path to operational data; typed operations; scope-bound | **Exists**: in-memory and MongoDB |
| MongoDB Community | Per-customer operational database behind the Store | **Exists** for synthetic runs ([0015](adr/0015-mongodb-store-layout.md)) |
| Acquisition (Treg) | Data acquisition, recorded as `SourceEvidence` | First route built, tested on synthetic replies ([0008](adr/0008-treg-for-acquisition.md), [0017](adr/0017-first-treg-route.md)) |
| Judgment and synthesis | Deterministic rules and play templates; model engines only after evaluation | **Exists** ([0009](adr/0009-judgment-and-synthesis-engines.md)) |
| Task scheduler | Exactly one; Herdr or Foreman | **Open** ([0007](adr/0007-single-task-scheduler.md)) |
| Delivery adapter | Thin hand-off to EmailBison/Instantly; simulated first | Simulated gate exists; platforms not built ([0006](adr/0006-delivery-through-existing-platforms.md)) |
| Projections | Sheets/Docs views generated from the Store | CSV, agent connector (live-checked) and service account (fake-tested) ([0003](adr/0003-systems-of-record.md), [0019](adr/0019-projection-writers-and-agent-connectors.md)) |

## Contracts

All contracts are immutable Pydantic models that reject unknown fields and carry a
`schema_version` ([ADR 0012](adr/0012-contract-conventions.md)).

| Contract | Key invariants enforced in code |
|---|---|
| `CustomerScope` | Synthetic scopes cannot deliver live; scopes cannot share namespaces |
| `SourceEvidence` | Acquired by a service or a human, never a model; retrieved means excerpt and hash; failed means a reason and no content |
| `QualificationDecision` | Facts and settled criteria cite evidence; hypotheses cannot settle a criterion; disqualifiers must be evidenced misses; citations must resolve to usable, in-scope evidence about the same account |
| `HumanApproval` | Human reviewers only; bound to an artifact hash and a scope; delivery approvals name a target; edits, expiry and revocation void it |
| `OperationalEvent` | Closed set of event types, each mapped to a loop stage and required record references; bounded flat attributes; idempotency key |
| `Artifact` | Content matches its hash; drafts have a recipient and briefs none; cites evidence; an edit is a new version that supersedes the old one |
| `Strategy` | Closed set of rule kinds; criteria, required criteria and plays resolve to each other; templates use only declared, plain placeholders |

The Store adds the checks that span records when it writes them: citations resolve to usable
evidence in the same scope, artifact versions form one line, approvals bind to a stored version
and its recipient, event refs resolve, and an idempotency key is stored once.

## First vertical slice

The slice runs end to end on synthetic fixtures, through the in-memory Store or MongoDB:
`uv run auggie run examples/synthetic/northstar-msp [--mongodb URI]`. From `inputs/` (scope, TOML strategy,
account, recorded responses, timeline and scripted human decisions) it produces every record in
`scenario.json` and the exact bytes of `draft.md`; the tests compare them.

| Step | Stage | Records | Status |
|---|---|---|---|
| Research | Observe | `SourceEvidence` (including a failed retrieval), `evidence.recorded` | Runs; fixture replay |
| Validate evidence | Validate | `evidence.validated`, `evidence.rejected` | Runs |
| Qualify | Judge | `QualificationDecision`, `qualification.decided` | Runs; deterministic rules |
| Draft | Compose | `Artifact`, `draft.composed` | Runs; strategy play template |
| Human approval | Approve | Two `HumanApproval` records (content, delivery), `approval.recorded` | Runs; scripted decisions |
| Simulated delivery | Deliver | `delivery.simulated`, authorized by the delivery approval | Runs; simulated |
| Record outcome | Learn | `outcome.recorded` | Runs; reported outcome |

The automated run stops at the draft. Approval, delivery and outcome are separate calls a person
triggers. Until the scheduler decision is made, the slice runs as a plain sequential call chain:
no retries, leases or background execution.

These paths are blocked, each by a test: a decision or draft citing a failed source; delivery of a
draft edited after approval, or of the version it replaced; delivery without both a content and a
delivery approval in force for that version and recipient; and any record crossing a customer
scope.

## Package layout

```text
src/auggie/
  contracts/     Exists    versioned data shapes and invariants; no I/O
  store/         Exists    typed Store API; in-memory reference and MongoDB (ADR 0015)
  strategy.py    Exists    load a TOML strategy file pinned to its exact bytes
  accounts.py    Exists    account and recipient inputs
  credentials.py Exists    per-customer secret files (ADR 0016)
  spend.py       Exists    per-run spend limit (ADR 0016)
  acquisition/   Exists    fixture replay, evidence validation, Treg page text (ADR 0017)
  judgment/      Exists    deterministic rules behind the Qualifier interface (ADR 0009)
  compose/       Exists    draft composition from strategy play templates; briefs proposed
  approval/      Exists    recording a reviewer's decision; review workflow proposed
  delivery/      Exists    authorization gate, simulated only; EmailBison/Instantly later
  pipeline.py    Exists    the slice as plain sequential calls
  synthetic.py   Exists    run a synthetic scenario from its inputs
  cli.py         Exists    `auggie run`, `auggie project`, `auggie treg-fetch`
  external.py    Exists    the gate in front of every external call
  projections/   Exists    read-only tabs: CSV, agent workbook + verify, Google Sheet (ADR 0019)
examples/synthetic/  Exists  synthetic fixtures only
tests/               Exists  including the shared Store contract suite in tests/store/
```

Scheduler integration has no package until ADR 0007 is decided.

## Open questions

These have no decision yet. Each needs an ADR before work depends on it.

- Task scheduler: Herdr or Foreman ([ADR 0007](adr/0007-single-task-scheduler.md)).
- Whether a model engine (Jev, OpenRouter) ever earns a place, and its calibration thresholds
  ([ADR 0009](adr/0009-judgment-and-synthesis-engines.md)).
- Per-customer deployment topology and sizing.
- Authentication and usage terms for provider CLIs in production, and Treg's and Exa's
  retention terms for fetched content ([ADR 0017](adr/0017-first-treg-route.md)).
- How edits and approvals made in Sheets/Docs come back as proposals. (Token refresh is settled
  for service accounts by [ADR 0019](adr/0019-projection-writers-and-agent-connectors.md).)
- Customer data ownership, retention and offboarding.
- Backup, restore and monitoring.
- Whether outcomes become a first-class contract or stay as events.

## Related repositories

- [Auggie Research](https://github.com/AuggieGTM/auggie-research) and
  [Dale](https://github.com/AuggieGTM/dale): separate public repositories.
- The maintainer's private projects: nothing from them is copied here without a licensing and
  confidentiality review.

This harness does not rebuild their functionality.
