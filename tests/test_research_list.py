"""Researching a list of prospects in one run, within one cap (ADR 0027). Synthetic list,
recorded responses and a fake Treg; nothing leaves this machine."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from auggie import cli
from auggie.acquisition.fixture import (
    FixtureAcquisition,
    RecordedListing,
    RecordedListings,
    RecordedResponse,
)
from auggie.acquisition.treg import CEILING_MICRO_USD, TregPageText
from auggie.cli import main
from auggie.contracts import CustomerScope
from auggie.credentials import CredentialFiles
from auggie.operate import replay_clock, research_list
from auggie.prospect_list import ProspectFolder, compare, read_list, write_new
from auggie.spend import RunBudget
from auggie.store import Store
from auggie.store.memory import InMemoryBackend
from auggie.strategy import load_strategy
from tests.conftest import EXAMPLES, MONGODB_URI, mongo_backend
from tests.test_treg import TOKEN_VALUE, Replay, page

KESTREL = EXAMPLES / "kestrel-security"
SCOPE = CustomerScope.model_validate_json((KESTREL / "scope.json").read_text(encoding="utf-8"))
RULES = load_strategy(KESTREL / "inputs", "strategy/security.toml", commit="0" * 40, scope=SCOPE)
REPLAY = json.loads((KESTREL / "list-replay.json").read_text(encoding="utf-8"))
REPO = Path(__file__).resolve().parent.parent
STRATEGY = [
    "--strategy-repo",
    str(REPO),
    "--strategy-path",
    "examples/synthetic/kestrel-security/inputs/strategy/security.toml",
]


def prospects(scope: CustomerScope = SCOPE) -> list[Any]:
    text = (KESTREL / "prospects.csv").read_text(encoding="utf-8")
    return [r.account for r in read_list(text, scope=scope, strategy=RULES.strategy)]


def recorded() -> tuple[FixtureAcquisition, RecordedListings, Any]:
    responses = [RecordedResponse.model_validate(r) for r in REPLAY["responses"]]
    listings = [RecordedListing.model_validate(r) for r in REPLAY["listings"]]
    return (
        FixtureAcquisition(responses),
        RecordedListings(listings),
        replay_clock(responses, listings),
    )


def rehearse(store: Store, **changes: Any) -> list[Any]:
    acquisition, listings, clock = recorded()
    arguments: dict[str, Any] = {
        "strategy": RULES,
        "prospects": prospects(),
        "acquisition": acquisition,
        "operator": "demo-seller",
        "budget": None,
        "page_ceiling_micro_usd": CEILING_MICRO_USD,
        "now": clock,
        "listings": listings,
    }
    arguments.update(changes)
    return research_list(store, **arguments)


def test_a_list_is_researched_in_one_run_and_resumes_without_repeating() -> None:
    store = InMemoryBackend().open(SCOPE)
    lines = rehearse(store)
    assert [(line.account_id, line.status) for line in lines] == [
        ("acc-fernhollow", "researched"),
        ("acc-brightquarry", "researched"),
        ("acc-saltmarsh", "researched"),
    ]
    fernhollow, brightquarry, saltmarsh = (line.result for line in lines)
    assert fernhollow.draft is not None and fernhollow.decision.outcome == "qualified"
    # One detection role: too few, so a person reviews it, and no draft is written.
    assert brightquarry.decision.outcome == "needs_review" and brightquarry.draft is None
    # A page that failed is recorded as failed, and the list goes on.
    assert saltmarsh.verdicts[0].evidence.status == "failed"
    runs = {line.result.decision.decision_id for line in lines}
    assert len(runs) == 3  # each account is its own research
    again = rehearse(store)
    assert [line.status for line in again] == ["skipped"] * 3
    assert again[0].detail == f"already researched ({fernhollow.decision.decision_id})"
    assert [line.status for line in rehearse(store, again=True)] == ["researched"] * 3


def test_an_error_stops_the_list_and_the_rest_wait() -> None:
    store = InMemoryBackend().open(SCOPE)
    without_brightquarry = FixtureAcquisition(
        RecordedResponse.model_validate(r)
        for r in REPLAY["responses"]
        if "brightquarry" not in r["url"]
    )
    lines = rehearse(store, acquisition=without_brightquarry)
    assert [(line.account_id, line.status) for line in lines] == [
        ("acc-fernhollow", "researched"),
        ("acc-brightquarry", "stopped"),
        ("acc-saltmarsh", "waiting"),
    ]
    assert lines[1].detail.startswith("LookupError: no recorded response for")


def paid(limit: int, tmp_path: Path) -> tuple[CustomerScope, RunBudget, TregPageText, Replay]:
    execution = SCOPE.execution.model_copy(
        update={"external_providers": True, "max_run_spend_micro_usd": 10_000}
    )
    scope = SCOPE.model_copy(update={"execution": execution})
    folder = tmp_path / "credentials" / scope.credential_namespace
    folder.mkdir(mode=0o700, parents=True)
    (folder / "treg-token").write_text(TOKEN_VALUE, encoding="utf-8")
    (folder / "treg-token").chmod(0o600)
    pages = {r["url"]: page(r["url"], r.get("excerpt") or "a page") for r in REPLAY["responses"]}
    treg = Replay(pages)
    budget = RunBudget(limit)
    acquisition = TregPageText(
        scope=scope,
        credentials=CredentialFiles(tmp_path / "credentials"),
        budget=budget,
        transport=treg,
    )
    return scope, budget, acquisition, treg


def test_the_whole_list_shares_one_cap_and_an_account_starts_only_if_it_fits(
    tmp_path: Path,
) -> None:
    # Each account names one page; the fake Treg charges 1,000 of its 2,000 ceiling.
    scope, budget, acquisition, treg = paid(3_000, tmp_path)
    store = InMemoryBackend().open(scope)
    lines = rehearse(store, prospects=prospects(scope), acquisition=acquisition, budget=budget)
    assert [(line.account_id, line.status) for line in lines] == [
        ("acc-fernhollow", "researched"),
        ("acc-brightquarry", "researched"),
        ("acc-saltmarsh", "waiting"),
    ]
    assert [line.spent_micro_usd for line in lines[:2]] == [1000, 1000]
    assert lines[2].detail == "needs up to 2000 micro-USD, and 1000 of 3000 is left"
    assert budget.spent_micro_usd == 2000 and len(treg.requests) == 2
    # The next run resumes with the account that waited.
    _, budget, acquisition, _ = paid(3_000, tmp_path / "next")
    resumed = rehearse(store, prospects=prospects(scope), acquisition=acquisition, budget=budget)
    assert [line.status for line in resumed] == ["skipped", "skipped", "researched"]


# The command


def folder_of_prospects(tmp_path: Path) -> Path:
    path = tmp_path / "prospects"
    text = (KESTREL / "prospects.csv").read_text(encoding="utf-8")
    with ProspectFolder.for_writing(path) as folder:
        write_new(compare(read_list(text, scope=SCOPE, strategy=RULES.strategy), folder), folder)
    return path


def test_the_command_rehearses_a_list_and_reports_each_account(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = folder_of_prospects(tmp_path)
    with mongo_backend() as backend:
        argv = [
            "research-list",
            str(KESTREL / "scope.json"),
            "--prospects",
            str(folder),
            *STRATEGY,
            "--operator",
            "demo-seller",
            "--replay",
            str(KESTREL / "list-replay.json"),
            "--mongodb",
            os.environ[MONGODB_URI],
            "--database-prefix",
            backend.database_prefix,
        ]
        assert main(argv) == 0
        out = capsys.readouterr().out
        assert "demo-security: research of 3 prospects from recorded responses" in out
        assert (
            "researched acc-fernhollow" in out and "qualified, draft draft-fernhollow-run-" in out
        )
        assert "3 researched, 0 skipped, 0 waiting; 1 draft to review" in out
        assert "next      auggie review ... --next --reviewer <you>" in out
        assert main(argv) == 0
        assert "0 researched, 3 skipped, 0 waiting; 0 drafts to review" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("extra", "external", "refusal"),
    [
        ([], False, "does not allow calls to external services"),
        (["--limit", "999999"], True, "--limit must be between 0 and the scope's run limit"),
    ],
    ids=["no-external-calls", "limit-above-scope"],
)
def test_a_list_that_cannot_run_is_refused_before_the_store_opens(
    extra: list[str],
    external: bool,
    refusal: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pymongo import MongoClient

    scope = SCOPE.model_copy(
        update={
            "execution": SCOPE.execution.model_copy(
                update={"external_providers": external, "max_run_spend_micro_usd": 4000}
            )
        }
    )
    scope_file = tmp_path / "scope.json"
    scope_file.write_text(scope.model_dump_json(), encoding="utf-8")
    treg = Replay(page("https://fernhollow.example/security", "x"))
    monkeypatch.setattr(cli, "treg_transport", lambda: treg)
    folder = folder_of_prospects(tmp_path)
    with mongo_backend() as backend:
        argv = [
            "research-list",
            str(scope_file),
            "--prospects",
            str(folder),
            *STRATEGY,
            "--operator",
            "demo-seller",
            *extra,
            "--mongodb",
            os.environ[MONGODB_URI],
            "--database-prefix",
            backend.database_prefix,
        ]
        assert main(argv) == 1
        assert refusal in capsys.readouterr().out
        client: MongoClient[dict[str, Any]] = MongoClient(os.environ[MONGODB_URI])
        try:
            names = client.list_database_names()
        finally:
            client.close()
        assert not [n for n in names if n.startswith(backend.database_prefix)]
    assert treg.requests == []


# Reviewing the drafts a list left waiting


def test_waiting_drafts_are_the_undecided_ones_oldest_first() -> None:
    from auggie.approval import ReviewDecision
    from auggie.contracts import Actor
    from auggie.operate import waiting_drafts
    from auggie.pipeline import record_review, record_revision

    store = InMemoryBackend().open(SCOPE)
    composed = [
        line.result.draft.ref.artifact_id
        for run in (rehearse(store), rehearse(store, again=True))
        for line in run
        if line.result is not None and line.result.draft is not None
    ]
    first, second = waiting_drafts(store)
    assert [first, second] == composed  # the oldest first
    person = Actor(kind="human", id="demo-reviewer")
    draft = store.latest_artifact(first)
    record_review(
        store,
        shown=draft.ref,
        review=ReviewDecision(
            scope="content",
            decision="changes_requested",
            reviewer=person,
            decided_at=draft.composed_at,
        ),
        approval_id="apr-first-content",
    )
    assert waiting_drafts(store) == [second]
    # A person's edit is a new version, and it waits for its own review.
    record_revision(
        store,
        previous=draft.ref,
        content=draft.content.replace("Riley\n", "Riley Moss\n"),
        by=person,
        at=draft.composed_at,
    )
    assert waiting_drafts(store) == [first, second]


def test_review_next_steps_through_the_drafts_each_approved_on_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = folder_of_prospects(tmp_path)
    answers = iter(["y", "", "y", "y", "n", "Not this one."])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(cli, "interactive", lambda: True)
    with mongo_backend() as backend:
        store = ["--mongodb", os.environ[MONGODB_URI], "--database-prefix", backend.database_prefix]
        research = [
            "research-list",
            str(KESTREL / "scope.json"),
            "--prospects",
            str(folder),
            *STRATEGY,
            "--operator",
            "demo-seller",
            "--replay",
            str(KESTREL / "list-replay.json"),
            *store,
        ]
        assert main(research) == 0
        assert main([*research, "--again"]) == 0
        capsys.readouterr()
        review = ["review", str(KESTREL / "scope.json"), "--next", "--reviewer", "demo-reviewer"]
        assert main([*review, *store]) == 0
        out = capsys.readouterr().out
        assert out.count("next      draft-fernhollow-run-") == 2
        assert "the oldest of 2 waiting" in out and "the oldest of 1 waiting" in out
        assert "recorded  content approved" in out and "recorded  delivery approved" in out
        assert "recorded  content rejected" in out
        assert out.rstrip().endswith("no more drafts are waiting")
        assert main([*review, *store]) == 0
        assert "no drafts are waiting for a person" in capsys.readouterr().out


# What Pi's review found


@pytest.fixture(params=["memory", "mongodb"])
def backend(request: pytest.FixtureRequest) -> Any:
    if request.param == "memory":
        yield InMemoryBackend()
    else:
        with mongo_backend() as mongo:
            yield mongo


def test_resuming_finishes_a_draft_whose_event_an_earlier_run_left_unrecorded(
    backend: Any,
) -> None:
    from auggie.operate import waiting_drafts

    store = backend.open(SCOPE)
    real = store.append_event

    def fails_once(event: Any) -> Any:
        if event.type == "draft.composed":
            store.append_event = real
            raise OSError("the connection dropped")
        return real(event)

    store.append_event = fails_once
    lines = rehearse(store)
    assert lines[0].status == "stopped" and waiting_drafts(store) == []
    resumed = rehearse(store)
    assert resumed[0].status == "skipped"
    assert resumed[0].detail.endswith("recorded its draft, which that run left unfinished")
    assert [d.startswith("draft-fernhollow-run-") for d in waiting_drafts(store)] == [True]
    assert rehearse(store)[0].detail.startswith("already researched")  # recorded once


def test_the_lists_budget_must_be_the_one_its_acquisition_spends(tmp_path: Path) -> None:
    scope, _, acquisition, treg = paid(10_000, tmp_path)
    store = InMemoryBackend().open(scope)
    with pytest.raises(ValueError, match="must be the one its acquisition spends from"):
        rehearse(store, prospects=prospects(scope), acquisition=acquisition, budget=RunBudget(2000))
    with pytest.raises(ValueError, match="must be the one its acquisition spends from"):
        rehearse(store, prospects=prospects(scope), acquisition=acquisition, budget=None)
    assert treg.requests == [] and store.list_events() == ()


def test_a_foreign_or_repeated_prospect_refuses_the_whole_list() -> None:
    from auggie.contracts import ScopeViolation

    store = InMemoryBackend().open(SCOPE)
    rehearse(store)
    events = store.list_events()
    foreign = prospects()[0].model_copy(update={"customer_id": "another-seller"})
    with pytest.raises(ScopeViolation):
        rehearse(store, prospects=[foreign])
    with pytest.raises(ValueError, match="names an account twice"):
        rehearse(store, prospects=[prospects()[0], prospects()[0]])
    assert store.list_events() == events


def test_an_account_whose_ids_would_be_too_long_is_stopped_before_paying(tmp_path: Path) -> None:
    from auggie.strategy import LoadedStrategy

    data = RULES.strategy.model_dump(mode="json")
    data["sources"]["security-and-compliance-certifications"] = {"label": "certifications page"}
    wide = LoadedStrategy(type(RULES.strategy).model_validate(data), RULES.ref)
    scope, budget, acquisition, treg = paid(6_000, tmp_path)
    page_url = "https://fernhollow.example/security"
    long = prospects(scope)[0].model_copy(
        update={
            "key": "f" * 64,
            "sources": {"security": page_url, "security-and-compliance-certifications": page_url},
        }
    )
    store = InMemoryBackend().open(scope)
    (line,) = rehearse(
        store, strategy=wide, prospects=[long], acquisition=acquisition, budget=budget
    )
    assert line.status == "stopped" and "record IDs would be too long" in line.detail
    assert treg.requests == [] and line.spent_micro_usd == 0


def test_a_stopped_account_reports_what_it_spent(tmp_path: Path) -> None:
    scope, budget, acquisition, treg = paid(6_000, tmp_path)
    store = InMemoryBackend().open(scope)
    # The page is read and paid for; then the listing has no recorded reply.
    (line, *_) = rehearse(
        store,
        prospects=prospects(scope),
        acquisition=acquisition,
        budget=budget,
        listings=RecordedListings([]),
    )
    assert line.status == "stopped" and line.detail.startswith("LookupError")
    assert line.spent_micro_usd == 1000 and len(treg.requests) == 1


def test_two_files_holding_one_account_differently_are_both_left_out(tmp_path: Path) -> None:
    from auggie.queries import load_prospects

    folder = folder_of_prospects(tmp_path)
    original = json.loads((folder / "fernhollow.json").read_text())
    (folder / "fernhollow-copy.json").write_text(json.dumps(original))
    found, notes = load_prospects(folder, SCOPE)
    assert "acc-fernhollow" in found and "fernhollow.json: repeats fernhollow-copy.json" in notes
    changed = {
        **original,
        "recipient": {**original["recipient"], "email": "other@fernhollow.example"},
    }
    (folder / "fernhollow-copy.json").write_text(json.dumps(changed))
    found, notes = load_prospects(folder, SCOPE)
    assert "acc-fernhollow" not in found
    assert (
        "fernhollow.json: holds acc-fernhollow as fernhollow-copy.json does, differently" in notes
    )
    assert "fernhollow-copy.json: not used, since another file holds acc-fernhollow" in notes


@pytest.mark.parametrize(
    ("change", "refusal"),
    [
        (["--strategy-path", "examples/synthetic/kestrel-security/missing.toml"], "StrategyError"),
        (
            ["--strategy-path", "examples/synthetic/northstar-msp/inputs/strategy/msp.toml"],
            "ScopeViolation",
        ),
        (["--replay", "missing-replay.json"], "FileNotFoundError"),
        (["--operator", ""], "ValidationError"),
    ],
    ids=["no-strategy", "another-scopes-strategy", "no-replay-file", "no-operator"],
)
def test_a_list_with_bad_inputs_is_refused_before_the_store_opens(
    change: list[str], refusal: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from pymongo import MongoClient

    folder = folder_of_prospects(tmp_path)
    with mongo_backend() as backend:
        options = {
            "--strategy-path": STRATEGY[3],
            "--operator": "demo-seller",
            "--replay": str(KESTREL / "list-replay.json"),
        }
        options[change[0]] = change[1]
        argv = [
            "research-list",
            str(KESTREL / "scope.json"),
            "--prospects",
            str(folder),
            "--strategy-repo",
            str(REPO),
            *(item for pair in options.items() for item in pair),
            "--mongodb",
            os.environ[MONGODB_URI],
            "--database-prefix",
            backend.database_prefix,
        ]
        assert main(argv) == 1
        assert f"stopped   {refusal}" in capsys.readouterr().out
        client: MongoClient[dict[str, Any]] = MongoClient(os.environ[MONGODB_URI])
        try:
            names = client.list_database_names()
        finally:
            client.close()
        assert not [n for n in names if n.startswith(backend.database_prefix)]
