# Auggie Harness

> **Status: foundation only.** This repository contains design documents, decision records and
> typed contracts with tests. It does not run a GTM workflow yet. There is no installer, no live
> integration and no quickstart. Anything described below as planned or proposed is not built.

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
- `examples/synthetic/` holds one fully synthetic scenario that walks the first planned vertical
  slice: research, validate evidence, qualify, draft, human approval, simulated delivery, record
  outcome.
- `tests/` checks the contracts' invariants and the synthetic scenario.

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
