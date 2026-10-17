# Auggie Harness

> **Status: early, synthetic only.** The harness runs one synthetic scenario end to end:
> recorded sources, deterministic rules, a template draft, scripted human approvals and
> simulated delivery, with records kept in memory or in a local MongoDB. One Treg acquisition
> route exists for operators to try; the scenario and the tests never call a provider, and nothing
> is sent. There is no installer. Anything described below as planned or proposed is not built.

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
time. Auggie is **not** an autonomous mass-outreach tool and not a hosted SaaS product.

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
- `src/auggie/pipeline.py` runs the first vertical slice as plain sequential calls: research,
  validate evidence, qualify, draft, human approval, simulated delivery, record outcome.
- `examples/synthetic/northstar-msp/` holds one fully synthetic scenario: its inputs, and the
  records the workflow must produce from them.
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

## Documentation

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
