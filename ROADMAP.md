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

## M3: First live routes (In progress)

- One Treg acquisition route ([ADR 0008](docs/adr/0008-treg-for-acquisition.md)). *Built:
  `exa.web.contents.get` over HTTP, one Treg team per scope
  ([ADR 0017](docs/adr/0017-first-treg-route.md)), tested against synthetic replies. Live-checked
  on 2026-10-18 through the harness's adapter with a test team's agent token: the request and
  Treg's 402 refusal (an unfunded team) worked end to end, and nothing was charged. Treg's 402
  carries no charge header, so the run budget counted the full ceiling, as ADR 0016 requires. A
  paid page fetch waits for a funded team and confirmed Treg and Exa retention terms.*
- A judgment engine chosen ([ADR 0009](docs/adr/0009-judgment-and-synthesis-engines.md)). *Decided:
  rules for judgment, templates for synthesis.*
- Read-only Sheets projection; Docs later. *Built: managed tabs regenerated from the Store
  (five since M4 added hiring), with no page text, written as CSV, by an operator's agent
  connector, or by a scope's own service account
  ([ADR 0019](docs/adr/0019-projection-writers-and-agent-connectors.md)).
  The connector path passed a live check on 2026-10-17 on synthetic Northstar data: the grid
  read-back showed every tab at exactly the projection's size, from A1, every cell literal text
  and equal. The service-account path is tested against a fake Google only.*
- The operator's flow for one prospect. *Built: `auggie research`, `review`, `revise`,
  `deliver` and `outcome`, each a separate command a person runs, with `review` and `revise`
  refusing to run outside a terminal. `revise` stores a person's edit as the draft's next
  version, which needs its own review ([ADR 0014](docs/adr/0014-artifact-contract.md)).
  [Your first prospect](docs/first-prospect.md) walks a seller through a rehearsal and a real
  prospect, and [Set up with your coding agent](docs/agent-setup.md) gives an agent the same
  steps, leaving review, revision, credentials and spending to the person. On 2026-10-18 a
  fresh agent followed the setup prompt in a clean clone: setup, checks, a replayed rehearsal, a
  refused early delivery and, after a person's review in their own terminal, delivery, outcome
  and projection. That run predates `revise`. The real-prospect prompt is reviewed but not yet
  run.*
- Delivery to prospects stays simulated.

**Next:** a controlled live run on the maintainer's own prospect, once the terms and policy
checks are done: one paid Treg page fetch, and one Gmail send from the maintainer to themself.

**Gate:** per-scope credentials; provider spend bounded per run; no delivery to a prospect.
*Credential files and the per-run spend limit are built
([ADR 0016](docs/adr/0016-credentials-and-spend-limits.md)).*
**Decision needed first:** the task scheduler ([ADR 0007](docs/adr/0007-single-task-scheduler.md)),
if any work must run in the background. *M3 runs stay operator-triggered from the CLI, so ADR 0007
can stay open.*

## M4: Signals (In progress)

Who is hiring for what, which technologies an account runs, and what changed, in the seller's
own vocabulary.

- The seller's strategy holds the vocabulary: hiring families, pattern thresholds and stack
  terms ([ADR 0023](docs/adr/0023-signal-vocabulary-in-the-strategy.md)). *Built: the strategy
  format, the matching and tally engine for ADR 0024's table, and stack terms judged from stored
  pages, with a second synthetic seller, a security vendor.*
- Careers snapshots, read whole from listing services' public feeds, and hiring tallies counted
  by rules ([ADR 0024](docs/adr/0024-careers-snapshots-and-hiring-tallies.md)). *Built: readers
  for Lever, Greenhouse and Ashby, tested on synthetic replies only; research counts hiring from
  the run's read, and findings and drafts cite their snapshots. The second seller's scenario
  qualifies and drafts from a replayed listing. Not built: a play field that quotes a hiring
  rationale, and Lever's EU-hosted boards. None of the three services documents when a role
  was posted, so the long-listed pattern is never met for them: it stays unknown for a family
  with open roles.*
- A hiring tab in the workbook. *Built.*
- The research shell: `auggie shell` for questions across accounts and the whole flow, with
  each question also a single command that prints JSON for a coding agent
  ([ADR 0025](docs/adr/0025-the-research-shell.md)). *Built: `accounts`, `brief`, `find`,
  `stack`, `hiring`, `fetch`, `research` and the flow, tested with scripted answers.*

**Gate:** two synthetic sellers in different domains qualify and draft from their own
vocabulary, with no domain words in the code; every tally line cites a dated snapshot and its
role links; a failed listing read leaves its criteria unknown. *Met on synthetic data on
2026-10-18: the IT-services and security sellers both qualify and draft, and the tests cover
each part. No listing has been read live yet.*

## M5: The seller's point of view (Planned)

- Proof points as exact claims from the seller's case studies, each with one source and marked
  as a vendor claim, and pains with where the seller fits, where it stops, and its approved
  offer, all in the seller's private strategy repository.
- Plays select by signal, and a draft pairs the account's evidence with a proof and an offer.
  Drafts stay templates. *Decided: [ADR 0026](docs/adr/0026-the-sellers-point-of-view.md).*

**Gate:** every proof in a draft is a vendor claim with exactly one source, quoted exactly; a
person still approves each draft.

## M6: Many accounts (Planned)

- A prospect list imported from CSV, previewed before anything is written.
- Research over the whole list in one foreground run, within a spending cap for the batch.
- `review --next` through the drafts waiting for a person.

**Gate:** a list of accounts is researched in one run within its cap, and each draft is still
approved on its own.

## M7: Proven in real deployments (Planned)

Sellers run the harness for their own prospecting outside this repository, as does the
maintainer's own private service. Their configuration, strategy and data stay in their own
private repositories and systems. Only generic fixes, and synthetic regression fixtures that
reproduce what was learned, land here.

**Gate:** two independent deployments differ by configuration only.

## M8: Delivery and learning (Later)

- Delivery adapters behind delivery approval, one per sending platform: the seller's own
  mailbox, Gmail and then Microsoft 365
  ([ADR 0020](docs/adr/0020-one-delivery-adapter-per-sending-platform.md)).
  First, how the seller obtains, stores and renews the mailbox credential, since the harness
  never writes secrets ([ADR 0016](docs/adr/0016-credentials-and-spend-limits.md)).
  [ADR 0021](docs/adr/0021-the-sellers-gmail-credential.md) answers it for Gmail: a one-time
  sign-in the seller runs, and a send-only token the harness only reads. *Built for Gmail: the
  adapter, its sign-in script, person records and the attempt, sent, failed and unknown records
  of [ADR 0022](docs/adr/0022-live-delivery-records.md), tested against a fake Google. Nothing
  has been sent through it yet.*
- Outcome intake and strategy-revision proposals that people review in Git.

## Later, each its own decision

- A weekly refresh of watched accounts, which needs the scheduler
  ([ADR 0007](docs/adr/0007-single-task-scheduler.md), Open).
- Discovery: accounts hiring for the seller's vocabulary, proposed for the seller to accept.
- Drafts worded by a model, where code decides every fact and a sentence without a source is
  dropped ([ADR 0009](docs/adr/0009-judgment-and-synthesis-engines.md) would be revisited).

## Public release readiness (Later)

`SECURITY.md`, `CONTRIBUTING.md`, third-party notices, and installation verified on fresh
machines.
