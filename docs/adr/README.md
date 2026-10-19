# Architecture decision records

Short records of decisions that shape architecture, data compatibility, installation, extension
behavior or the public boundary. The process is described in
[ADR 0001](0001-record-decisions.md).

| Status | Meaning |
|---|---|
| **Accepted** | Decided. Changed only by a new ADR that supersedes it. |
| **Proposed** | Recommended and awaiting maintainer acceptance. Don't build on it as if settled. |
| **Open** | A recorded question with no decision. Lists the constraints that already apply. |
| **Superseded** | Replaced; kept for history, with a link to its replacement. |

| ADR | Title | Status |
|---|---|---|
| [0001](0001-record-decisions.md) | Record decisions as public ADRs | Accepted |
| [0002](0002-python-toolchain.md) | Python 3.12+, Pydantic, uv and pytest | Accepted |
| [0003](0003-systems-of-record.md) | Git for approved strategy, MongoDB per customer for operations, Google for views | Accepted |
| [0004](0004-customer-isolation.md) | Isolate each customer's credentials, data, knowledge and execution | Accepted |
| [0005](0005-evidence-and-approval-gates.md) | Evidence before judgment; human approval before delivery | Accepted |
| [0006](0006-delivery-through-existing-platforms.md) | Deliver through existing sending platforms; no SMTP | Superseded by [0020](0020-one-delivery-adapter-per-sending-platform.md) |
| [0007](0007-single-task-scheduler.md) | One task scheduler: Herdr or Foreman | Open |
| [0008](0008-treg-for-acquisition.md) | Treg for data acquisition, behind an adapter | Superseded by [0024](0024-careers-snapshots-and-hiring-tallies.md) |
| [0009](0009-judgment-and-synthesis-engines.md) | Judgment and synthesis engines | Accepted |
| [0010](0010-public-repository-boundary.md) | Public repository boundary | Accepted |
| [0011](0011-sageox-engineering-context.md) | SageOx holds engineering context, not customer intelligence | Accepted |
| [0012](0012-contract-conventions.md) | Contract conventions and development checks | Accepted |
| [0013](0013-strategy-files.md) | Strategy files are TOML, one file per revision | Accepted |
| [0014](0014-artifact-contract.md) | One artifact contract for briefs and drafts | Accepted |
| [0015](0015-mongodb-store-layout.md) | MongoDB Store layout | Accepted |
| [0016](0016-credentials-and-spend-limits.md) | Per-customer credentials and run spend limits | Accepted |
| [0017](0017-first-treg-route.md) | First Treg route: page text over HTTP | Accepted |
| [0018](0018-sheets-projection.md) | Read-only Google Sheets projection | Superseded by [0019](0019-projection-writers-and-agent-connectors.md) |
| [0019](0019-projection-writers-and-agent-connectors.md) | Projection writers, and what an agent's connectors may do | Accepted |
| [0020](0020-one-delivery-adapter-per-sending-platform.md) | One delivery adapter per sending platform, the seller's mailbox first | Accepted |
| [0021](0021-the-sellers-gmail-credential.md) | The seller's Gmail credential | Accepted |
| [0022](0022-live-delivery-records.md) | Live delivery records | Accepted |
| [0023](0023-signal-vocabulary-in-the-strategy.md) | The seller's strategy holds the signal vocabulary | Accepted |
| [0024](0024-careers-snapshots-and-hiring-tallies.md) | Careers snapshots and hiring tallies | Accepted |
| [0025](0025-the-research-shell.md) | The research shell | Accepted |
| [0026](0026-the-sellers-point-of-view.md) | The seller's point of view: pains, proofs and offers | Accepted |
| [0027](0027-many-accounts.md) | Many accounts: importing a list, researching it, reviewing its drafts | Accepted |
| [0028](0028-learning-from-outcomes.md) | Learning from outcomes: a report, and proposals as a patch | Accepted |
| [0029](0029-reading-replies-from-gmail.md) | Reading replies from the seller's Gmail | Accepted |
| [0030](0030-screening-a-list-by-hiring.md) | Screening a list by hiring before paying to research it | Accepted |
