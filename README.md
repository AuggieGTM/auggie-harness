# Auggie Harness

> **Status: early.** The harness runs one synthetic scenario end to end: recorded sources,
> deterministic rules, a template draft, scripted human approvals and simulated delivery, with
> records kept in memory or in a local MongoDB. A seller can run the same steps for one real
> prospect. One Treg acquisition route and a Gmail delivery adapter exist, tested against
> synthetic replies; the Treg route has been live-checked only up to an unfunded refusal, and
> nothing has been sent through Gmail yet. The scenario and the tests never call a provider.
> There is no installer. Anything described below as planned or proposed is not built.

Auggie is a **GTM-as-code harness**. It turns versioned go-to-market strategy into an
evidence-to-action-to-learning loop:

**Define → Observe → Validate → Judge → Compose → Approve → Deliver → Learn**

| Stage | What happens |
|---|---|
| Define | Approved strategy (ICP, offers, voice, criteria) is versioned in Git. |
| Observe | Sources about an account are acquired and recorded as dated evidence. |
| Validate | Evidence is checked: retrieved, attributable, fresh, in scope. |
| Judge | The account is qualified against an exact strategy revision, citing evidence. |
| Compose | Briefs and drafts are written from validated evidence only. |
| Approve | A person approves one exact artifact version for one purpose. |
| Deliver | Approved drafts go out through an existing sending platform. |
| Learn | Outcomes are recorded and inform the next strategy revision, after human review. |

The goal is reliable account intelligence, thoughtful outreach and knowledge that accumulates over
time. Auggie is for a seller prospecting for the company they work at, not for an agency running
outreach for clients. It is **not** an autonomous mass-outreach tool and not a hosted SaaS
product.

## What exists today

- `src/auggie/contracts/` holds typed, immutable [Pydantic](https://docs.pydantic.dev/) contracts:
  - **`CustomerScope`**: one customer's isolated credentials, data, knowledge and execution boundary.
  - **`SourceEvidence`**: what a source said, when, and how it was obtained, including failed
    retrievals.
  - **`QualificationDecision`**: fit against an exact strategy revision, with evidence-backed
    findings.
  - **`HumanApproval`**: a reviewer's decision bound to an artifact hash and a scope.
  - **`OperationalEvent`**: append-only facts covering every stage of the loop.
  - **`Artifact`**: one exact version of a brief or draft, with what it was composed from.
  - **`Strategy`**: the validated shape of a TOML strategy file.
- `src/auggie/store/` is the typed Store API, bound to one customer scope, with in-memory and
  MongoDB implementations that pass the same contract tests.
- `src/auggie/acquisition/treg.py` fetches one page's text through Treg
  ([ADR 0017](docs/adr/0017-first-treg-route.md)), with per-customer credential files
  (`src/auggie/credentials.py`) and a per-run spend limit (`src/auggie/spend.py`,
  [ADR 0016](docs/adr/0016-credentials-and-spend-limits.md)). Tests replay synthetic replies from
  `examples/synthetic/treg/`.
- `src/auggie/delivery/gmail.py` sends an approved draft from the seller's own Gmail with a
  send-only permission ([ADR 0021](docs/adr/0021-the-sellers-gmail-credential.md)), recording
  each attempt and what came of it ([ADR 0022](docs/adr/0022-live-delivery-records.md)).
  `scripts/gmail_sign_in.py` saves its token. Tests use a fake Google.
- `src/auggie/projections/` turns the Store into read-only tabs (accounts, findings, evidence,
  drafts, hiring) without page text, written as CSV, by your agent's Google connector, or by a
  service account ([ADR 0019](docs/adr/0019-projection-writers-and-agent-connectors.md)).
- `src/auggie/shell.py` is the research shell: questions across a scope's accounts, one more page
  read, and the flow, at a terminal. `src/auggie/queries.py` gives the same answers as read-only
  commands that print JSON for a coding agent
  ([ADR 0025](docs/adr/0025-the-research-shell.md)).
- `src/auggie/signals/` matches the seller's own vocabulary and counts hiring tallies by rule
  ([ADR 0023](docs/adr/0023-signal-vocabulary-in-the-strategy.md),
  [ADR 0024](docs/adr/0024-careers-snapshots-and-hiring-tallies.md)).
  `src/auggie/acquisition/careers.py` reads a prospect's whole careers listing from Lever,
  Greenhouse or Ashby, free, into a careers snapshot. Research counts hiring from it, and
  `auggie hiring` shows each account's tally. Tested on synthetic replies only, in
  `examples/synthetic/listings/`.
- `src/auggie/proofs.py` checks the seller's proofs: customer stories in the strategy, each
  quoted in a draft only while a read of its page holds the claim word for word. Drafts pair
  the account's evidence with a pain, a proof and an offer
  ([ADR 0026](docs/adr/0026-the-sellers-point-of-view.md)). Tested on synthetic pages only.
- `src/auggie/pipeline.py` runs the first vertical slice as plain sequential calls: research,
  validate evidence, qualify, draft, human approval, simulated delivery, record outcome.
- `examples/synthetic/northstar-msp/` holds one fully synthetic scenario: its inputs, and the
  records the workflow must produce from them.
- `examples/synthetic/kestrel-security/` is a second synthetic seller, a security vendor. Its
  scenario qualifies from detection hiring on a replayed listing, and drafts from its pain and
  a proof quoted from its own case-study page.
- `tests/` checks the contracts, the Store contract, the slice and the paths it must refuse.

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync                    # create .venv and install dependencies
uv run pytest              # tests
uv run ruff check .        # lint
uv run ruff format --check .
uv run mypy                # strict type checking
```

These are development checks, not a product installation.

Run the synthetic scenario and print what each stage did:

```sh
uv run auggie run examples/synthetic/northstar-msp --show-draft --negative
```

`--negative` also tries the paths the slice must refuse (citing a failed source, delivering an
edited or unapproved draft, crossing customer scopes) and fails unless each is blocked.

To use MongoDB, start the official image and point the tests or the CLI at it (Docker works
the same way as Podman):

```sh
podman run -d --rm --name auggie-mongo -p 127.0.0.1:27017:27017 docker.io/library/mongo:8
AUGGIE_TEST_MONGODB_URI=mongodb://127.0.0.1:27017 uv run pytest   # MongoDB tests skip without it
uv run auggie run examples/synthetic/northstar-msp --mongodb mongodb://127.0.0.1:27017
```

The CLI writes to one database per scope (`auggie_demo_msp` for Northstar); `--database-prefix`
keeps a demo apart from other data on the same server. Outside development, install the
`auggie-harness[mongodb]` extra.

`uv run auggie run examples/synthetic/northstar-msp --csv out/` also writes the run's projection
as CSV. For a deployment's Store, `auggie project <scope.json> --mongodb URI --csv DIR` does the
same, and `--spreadsheet ID` writes the managed tabs of the scope's Google Sheet instead (with
the `auggie-harness[google]` extra and the scope's service account key).

### Writing a projection with your agent

If your agent (Claude, ChatGPT) has a Google Sheets connector, it can write the projection for
you, as you, with no Google Cloud setup:

1. Export the workbook: `auggie run <scenario> --workbook w.json`, or
   `auggie project <scope.json> --mongodb URI --workbook w.json`.
2. Ask your agent: *"Write the projection in `w.json` to my Google Sheet `<link>`, touching only
   the tabs it names. Add any that are missing. Resize each to exactly its size (the number of
   `rows`, and the length of a row), clear it, and write its `connector_rows` unchanged from A1.
   Then read those tabs back with `spreadsheets.get`, `includeGridData: true`, the tab names as
   ranges, and the fields `sheets.properties.title`, `sheets.properties.gridProperties`,
   `sheets.data.startRow`, `sheets.data.startColumn` and
   `sheets.data.rowData.values.userEnteredValue`, and save the reply, unedited, as `r.json`."*
3. Check it: `auggie verify-sheet w.json r.json`. It confirms each tab is exactly the
   projection's size, starts at A1, and holds every value as literal text. Only `match` counts.

`connector_rows` carry a leading apostrophe on every value, so the connector stores each one as
literal text instead of parsing it as a formula, number or date. The connector only ever writes
views: evidence and approvals always go through the harness itself.

## Documentation

- [Your first prospect](docs/first-prospect.md): rehearse the whole loop offline, then run it
  for one of your real prospects.
- [Set up with your coding agent](docs/agent-setup.md): prompts that have an agent do the
  setup, the rehearsal and your first real prospect, leaving review, credentials and spending
  to you.
- [Architecture](docs/architecture.md): the loop, systems of record, isolation, proposed layout.
- [Decision records](docs/adr/README.md): what is decided, what is proposed, what is open.
- [Roadmap](ROADMAP.md): milestones and their gates. No dates.
- [AGENTS.md](AGENTS.md): instructions for contributors and coding agents.

## Data and privacy

This repository is public. It contains **synthetic examples only**: reserved `.example` domains and
invented people and companies. Customer data, credentials and confidential strategy never belong
here. See [ADR 0010](docs/adr/0010-public-repository-boundary.md).

## Related projects

[Auggie Research](https://github.com/AuggieGTM/auggie-research) and
[Dale](https://github.com/AuggieGTM/dale) are separate repositories. This harness does not
replace them.

## License

Released under the [MIT License](LICENSE). Dependencies keep their own licenses; see
[ADR 0010](docs/adr/0010-public-repository-boundary.md).

Auggie is an independent project. It is not affiliated with or endorsed by MongoDB, Google or any
provider named in its documentation.
