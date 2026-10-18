# ADR 0025: The research shell

- **Status:** Accepted
- **Date:** 2026-10-18

## Context

Each step of the prospect flow is a separate command: `research`, `review`, `revise`, `deliver`,
`outcome`. A seller working many accounts also wants to ask for specific things: who is hiring
for a family, which technologies an account's pages name, whether a page says something, or one
more page read for one account. The harness has no way to ask anything yet.

## Decision

The maintainer chose all four:

- **Ask through a shell, and through your coding agent.** `auggie shell <scope.json> --mongodb
  URI` opens an interactive session for one scope.
  - Every question in it also exists as a single command that can print JSON, such as `auggie
    hiring ... --json`. So a coding agent can take a plain-English question, run those
    commands, and answer by quoting the records.
  - The harness itself uses no model to read questions. A model that turns plain English into
    commands would need [ADR 0009](0009-judgment-and-synthesis-engines.md) revisited.
- **The shell covers all accounts and the whole flow.**
  - Questions across the scope's accounts, or about one: `accounts`, `hiring`, `stack`, `find`
    (words in stored evidence) and `brief`.
  - Acquisition for one account: `fetch` (one page you name, recorded as evidence) and
    `research`.
  - The flow: `review`, `revise`, `deliver` and `outcome`.
- **Every paid or external call asks first.** Before each `fetch`, `research` or listing read,
  the shell says what it will call and the most it can spend, and waits for a yes. The scope's
  per-run limit applies to each command ([ADR 0016](0016-credentials-and-spend-limits.md)).
- **Order of work:** the strategy's signal vocabulary and the tally engine come first
  ([ADR 0023](0023-signal-vocabulary-in-the-strategy.md)), then the shell, then careers
  snapshots, readers and `hiring` ([ADR 0024](0024-careers-snapshots-and-hiring-tallies.md)).

The rules the shell keeps:

- **Answers come from records.** Each line names the record it rests on and when its source was
  read. Findings are shown as findings, with their citations, never as facts about the account.
- **Only the person approves.** The shell runs only at a terminal, like `review`, because
  approvals and edits happen inside it. The single JSON commands only read: they record nothing,
  so they may run without a terminal.
- **One command at a time.** The shell runs each command to the end before the next, and nothing
  in the background ([ADR 0007](0007-single-task-scheduler.md),
  [ADR 0015](0015-mongodb-store-layout.md)).

## Consequences

- The Store needs read queries across a scope's accounts: the accounts, their evidence, and
  their snapshots.
- JSON output is a view, like a projection
  ([ADR 0019](0019-projection-writers-and-agent-connectors.md)): nothing reads it back into the
  Store.
- A coding agent following [docs/agent-setup.md](../agent-setup.md) may run the read-only
  commands. It still leaves review, revise and every paid call to the person.
- Not built.
