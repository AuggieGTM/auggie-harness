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
from auggie.prospect_list import compare, read_list, write_new
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
    folder = tmp_path / "prospects"
    text = (KESTREL / "prospects.csv").read_text(encoding="utf-8")
    write_new(compare(read_list(text, scope=SCOPE, strategy=RULES.strategy), folder), folder)
    return folder


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
