"""Screening a list by hiring, free, before paying to research it (ADR 0030). The security
seller's synthetic list, recorded listing replies and a fake Treg; nothing leaves this
machine."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from auggie import shell
from auggie.acquisition.fixture import (
    RecordedListing,
    RecordedListings,
    RecordedResponse,
)
from auggie.acquisition.treg import TregPageText
from auggie.cli import main
from auggie.credentials import CredentialFiles
from auggie.operate import replay_clock, screen
from auggie.spend import RunBudget
from auggie.store.memory import InMemoryBackend
from auggie.strategy import load_strategy
from tests.conftest import EXAMPLES, MONGODB_URI, NORTHSTAR, mongo_backend
from tests.test_research_list import (
    REPLAY,
    RULES,
    SCOPE,
    STRATEGY,
    folder_of_prospects,
    paid,
    prospects,
)
from tests.test_treg import Replay

KESTREL = EXAMPLES / "kestrel-security"
LISTINGS = [RecordedListing.model_validate(r) for r in REPLAY["listings"]]
RESPONSES = [RecordedResponse.model_validate(r) for r in REPLAY["responses"]]


def screened(store: Any, **changes: Any) -> list[Any]:
    arguments: dict[str, Any] = {
        "strategy": RULES,
        "prospects": prospects(),
        "reader": RecordedListings(LISTINGS),
        "run_id": "run-screen",
    }
    arguments.update(changes)
    return screen(store, **arguments)


def test_the_screen_passes_only_accounts_hiring_as_the_strategy_requires() -> None:
    store = InMemoryBackend().open(SCOPE)
    lines = screened(store)
    assert [(line.account_id, line.status) for line in lines] == [
        ("acc-fernhollow", "passed"),
        ("acc-brightquarry", "screened out"),
        ("acc-saltmarsh", "screened out"),
    ]
    assert lines[0].detail.startswith("detection-hiring: detection engineering on the Lever")
    assert "1 open roles in the family" in lines[1].detail
    # Each read is a recorded snapshot, free; nothing is researched and nothing is paid.
    recorded = store.list_events(type="careers.recorded")
    assert len(recorded) == 3 and store.list_events(type="qualification.decided") == ()
    assert store.list_evidence() == ()


def test_an_account_the_screen_cannot_judge_is_left_out_with_why() -> None:
    store = InMemoryBackend().open(SCOPE)
    no_listing = prospects()[0].model_copy(update={"careers_listing": None})
    failed = LISTINGS[1].model_copy(update={"status": 503, "body": ""})
    lines = screened(
        store,
        prospects=[no_listing, prospects()[1]],
        reader=RecordedListings([failed]),
    )
    assert (lines[0].status, lines[0].detail) == (
        "not screened",
        "it names no careers listing",
    )
    assert lines[1].status == "not screened" and "HTTP 503" in lines[1].detail


def test_a_strategy_requiring_no_hiring_has_nothing_to_screen_by() -> None:
    from auggie.synthetic import load_inputs

    inputs = load_inputs(NORTHSTAR)
    rules = load_strategy(
        NORTHSTAR / "inputs", inputs.strategy.path, commit="0" * 40, scope=inputs.scope
    )
    store = InMemoryBackend().open(inputs.scope)
    with pytest.raises(ValueError, match="requires no hiring criterion"):
        screen(
            store,
            strategy=rules,
            prospects=[inputs.account],
            reader=RecordedListings([]),
            run_id="r",
        )
    assert store.list_events() == ()


def test_research_list_screen_researches_only_the_accounts_that_passed(
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
            "--screen",
            "--mongodb",
            os.environ[MONGODB_URI],
            "--database-prefix",
            backend.database_prefix,
        ]
        assert main(argv) == 0
        out = capsys.readouterr().out
    assert "passed       acc-fernhollow" in out and "screened out acc-brightquarry" in out
    assert "1 of 3 passed the screen; researching them" in out
    assert "researched acc-fernhollow" in out and "researched acc-brightquarry" not in out
    assert "1 researched, 0 skipped, 0 waiting; 1 draft to review" in out


# The shell


def session(tmp_path: Path, answers: list[str]) -> tuple[Any, list[str], list[str], Replay]:
    scope, _, _, treg = paid(10_000, tmp_path)
    store = InMemoryBackend().open(scope)
    asked: list[str] = []
    printed: list[str] = []
    pending = iter(answers)

    def ask(prompt: str) -> str:
        asked.append(prompt)
        try:
            return next(pending)
        except StopIteration:
            raise EOFError from None

    def fresh() -> TregPageText:
        return TregPageText(
            scope=scope,
            credentials=CredentialFiles(tmp_path / "credentials"),
            budget=RunBudget.for_scope(scope),
            transport=treg,
        )

    accounts = {p.account_id: p for p in prospects(scope)}
    return (
        shell.Session(
            store=store,
            me="demo-seller",
            ask=ask,
            out=printed.append,
            acquisition=fresh,
            listings=lambda: RecordedListings(LISTINGS),
            live=True,
            prospects=accounts,
            strategy=RULES,
            research_clock=replay_clock(RESPONSES, LISTINGS),
        ),
        asked,
        printed,
        treg,
    )


def test_the_shell_screens_asks_once_for_each_part_then_offers_review(tmp_path: Path) -> None:
    run, asked, printed, treg = session(
        tmp_path, ["research --hiring", "y", "y", "y", "y", "", "y"]
    )
    shell.run(run)
    questions = [q for q in asked if q != "auggie> "]
    assert questions[0] == "read 3 careers listings, free; go ahead? [y/N] "
    assert questions[1] == (
        "research 1 account: 1 page through Treg, at most $0.002, under this command's "
        "limit of $0.010; go ahead? [y/N] "
    )
    assert questions[2] == "1 draft waiting; review them now? [y/N] "
    assert len(treg.requests) == 1  # only the account that passed was paid for
    assert any(line.startswith("researched   acc-fernhollow") for line in printed)
    assert any(line.startswith("recorded  content approved") for line in printed)


def test_declining_either_question_reads_or_spends_nothing_more(tmp_path: Path) -> None:
    run, _, printed, treg = session(tmp_path, ["research --hiring", "n"])
    shell.run(run)
    assert "nothing read" in printed and run.store.list_events() == ()
    run, _, printed, treg = session(tmp_path / "second", ["research --hiring", "y", "n"])
    shell.run(run)
    assert "nothing researched" in printed and treg.requests == []
    assert run.store.list_events(type="qualification.decided") == ()


# What Pi's review found


def test_a_screen_finishes_a_draft_an_earlier_run_left_unrecorded() -> None:
    from auggie.operate import waiting_drafts
    from tests.test_research_list import rehearse

    store = InMemoryBackend().open(SCOPE)
    real = store.append_event

    def fails_once(event: Any) -> Any:
        if event.type == "draft.composed":
            store.append_event = real  # type: ignore[method-assign]
            raise OSError("the connection dropped")
        return real(event)

    store.append_event = fails_once  # type: ignore[method-assign]
    rehearse(store)
    assert waiting_drafts(store) == []
    lines = screened(store)
    assert lines[0].status == "skipped"
    assert lines[0].detail.endswith("recorded its draft, which that run left unfinished")
    assert len(waiting_drafts(store)) == 1


def test_a_list_naming_an_account_twice_is_refused_before_any_read() -> None:
    store = InMemoryBackend().open(SCOPE)
    with pytest.raises(ValueError, match="names an account twice"):
        screened(store, prospects=[prospects()[0], prospects()[0]])
    assert store.list_events() == ()


def test_a_strategy_with_nothing_to_screen_by_is_refused_before_connecting(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import subprocess
    import tomllib

    folder = folder_of_prospects(tmp_path)
    text = (KESTREL / "inputs" / "strategy" / "security.toml").read_text(encoding="utf-8")
    text = text.replace('required = ["detection-hiring"]', 'required = ["names-beaconlog"]')
    assert tomllib.loads(text)["required"] == ["names-beaconlog"]
    repo = tmp_path / "repo"
    (repo / "strategy").mkdir(parents=True)
    (repo / "strategy" / "s.toml").write_text(text, encoding="utf-8")
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "strategy"], check=True)
    subprocess.run([*git, "commit", "-qm", "s"], check=True)
    argv = [
        "research-list",
        str(KESTREL / "scope.json"),
        "--prospects",
        str(folder),
        "--strategy-repo",
        str(repo),
        "--strategy-path",
        "strategy/s.toml",
        "--operator",
        "demo-seller",
        "--replay",
        str(KESTREL / "list-replay.json"),
        "--screen",
        "--mongodb",
        "mongodb://127.0.0.1:1",  # never reached
    ]
    assert main(argv) == 1
    assert "requires no hiring criterion" in capsys.readouterr().out


def test_the_shell_counts_only_the_listings_it_will_read(tmp_path: Path) -> None:
    from tests.test_research_list import rehearse

    run, asked, printed, treg = session(tmp_path, ["research --hiring"])
    rehearse(run.store, prospects=list(run.prospects.values()))
    shell.run(run)
    assert [q for q in asked if "go ahead" in q] == []  # nothing to read, so nothing asked
    assert sum(line.startswith("skipped") for line in printed) == 3
    assert "0 of 3 passed the screen" in printed and treg.requests == []


def test_the_shell_refuses_a_strategy_with_nothing_to_screen_by_before_asking(
    tmp_path: Path,
) -> None:
    from auggie.strategy import LoadedStrategy

    data = RULES.strategy.model_dump(mode="json")
    data["required"] = ["names-beaconlog"]
    run, asked, printed, _ = session(tmp_path, ["research --hiring"])
    run.strategy = LoadedStrategy(type(RULES.strategy).model_validate(data), RULES.ref)
    shell.run(run)
    assert [q for q in asked if "go ahead" in q] == []
    assert any("requires no hiring criterion" in line for line in printed)
    assert run.store.list_events() == ()
