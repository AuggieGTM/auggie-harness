"""The research shell (ADR 0025), driven by scripted answers on synthetic records (offline)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from auggie import cli, shell
from auggie.accounts import Account
from auggie.acquisition.fixture import FixtureAcquisition, RecordedResponse
from auggie.cli import main
from auggie.contracts import CustomerScope
from auggie.operate import replay_clock
from auggie.queries import accounts_view
from auggie.store import Store
from auggie.store.memory import InMemoryBackend
from auggie.strategy import load_strategy
from auggie.synthetic import load_inputs
from tests.conftest import EXAMPLES, NORTHSTAR

SECURITY = EXAMPLES / "kestrel-security"


def drafts(store: Store) -> list[str]:
    return [
        ref.id
        for event in store.list_events(type="draft.composed")
        for ref in event.refs
        if ref.kind == "artifact"
    ]


def script(store: Store, *lines: str) -> tuple[Callable[[str], str], list[str]]:
    """Answers in order; ``{draft}`` becomes the newest draft's ID when the line is read."""
    asked: list[str] = []
    pending = iter(lines)

    def ask(prompt: str) -> str:
        asked.append(prompt)
        try:
            line = next(pending)
        except StopIteration:
            raise EOFError from None
        return line.format(draft=(drafts(store) or ["none"])[-1])

    return ask, asked


def northstar(live: bool = False) -> tuple[Store, Callable[..., shell.Session]]:
    inputs = load_inputs(NORTHSTAR)
    store = InMemoryBackend().open(inputs.scope)
    strategy = load_strategy(
        NORTHSTAR / "inputs", inputs.strategy.path, commit=inputs.strategy.commit, scope=store.scope
    )

    def session(ask: Callable[[str], str], out: Callable[[str], None]) -> shell.Session:
        return shell.Session(
            store=store,
            me="demo-seller",
            ask=ask,
            out=out,
            acquisition=lambda: FixtureAcquisition(inputs.responses),
            live=live,
            prospects={inputs.account.account_id: inputs.account},
            strategy=strategy,
            research_clock=replay_clock(inputs.responses),
            now=lambda: datetime(2026, 10, 14, 17, 0, tzinfo=UTC),
        )

    return store, session


def test_the_whole_flow_runs_in_one_session() -> None:
    store, session = northstar()
    printed: list[str] = []
    ask, _ = script(
        store,
        "accounts",
        "research acc-northstar",
        "accounts",
        "brief acc-northstar",
        'find acc-northstar "IT Operations Manager"',
        "find device management",
        "stack",
        "review {draft}",
        "y",
        "",
        "y",
        "deliver {draft}",
        "outcome {draft} positive_reply",
        "accounts",
        "quit",
    )
    assert shell.run(session(ask, printed.append)) == 0
    text = "\n".join(printed)
    assert "acc-northstar Northstar Works: not researched; 0 evidence; no drafts" in text
    assert "judged    acc-northstar qualified" in text
    assert "meets         active-it-hiring (source_fact)" in text
    assert "ev-northstar-news-run-" in text and "rejected: retrieval_failed" in text
    assert "ev-northstar-careers-run-" in text and '"' in text
    assert "the strategy defines no stack terms" in text
    assert "recorded  delivery approved" in text
    assert "simulated hand-off" in text and "positive_reply recorded" in text
    assert "(simulated)" in printed[-1]


def test_a_paid_read_asks_first_and_a_no_reads_nothing() -> None:
    store, session = northstar(live=True)
    url = "https://northstar.example/about/technology"
    printed: list[str] = []
    ask, asked = script(store, f"fetch acc-northstar {url}", "n", f"fetch acc-northstar {url}", "y")
    shell.run(session(ask, printed.append))
    assert any("one page through Treg, at most $0.002; go ahead?" in q for q in asked)
    assert "nothing read" in printed
    (evidence,) = store.list_evidence()
    assert str(evidence.source_url) == url
    assert any(line.startswith("recorded  ev-acc-northstar-page-") for line in printed)


def test_research_asks_before_its_paid_reads() -> None:
    store, session = northstar(live=True)
    printed: list[str] = []
    ask, asked = script(store, "research acc-northstar", "n")
    shell.run(session(ask, printed.append))
    assert any("3 pages through Treg, at most $0.006; go ahead?" in q for q in asked)
    assert store.list_evidence() == ()


def test_a_mistake_never_ends_the_session() -> None:
    store, session = northstar()
    printed: list[str] = []
    ask, _ = script(
        store,
        "frobnicate",
        "brief",
        "fetch acc-nowhere https://nowhere.example/",
        "research acc-nowhere",
        'find "unclosed',
        "review draft-nowhere",
        "outcome x maybe",
        "help",
    )
    assert shell.run(session(ask, printed.append)) == 0
    text = "\n".join(printed)
    assert "unknown command 'frobnicate'" in text
    assert "usage: brief <account>" in text
    assert "no account 'acc-nowhere' here" in text
    assert "no prospect file for 'acc-nowhere'" in text
    assert "No closing quotation" in text
    assert "an outcome is one of" in text
    assert "questions (answers from the records" in text


def test_stack_answers_from_the_sellers_own_vocabulary() -> None:
    scope = CustomerScope.model_validate_json((SECURITY / "scope.json").read_text())
    store = InMemoryBackend().open(scope)
    strategy = load_strategy(
        SECURITY / "inputs", "strategy/security.toml", commit="0" * 40, scope=scope
    )
    account = Account.model_validate(
        {
            "account_id": "acc-kestrel",
            "customer_id": "demo-security",
            "key": "kestrel",
            "name": "Kestrel Freight",
            "sources": {"security": "https://kestrel.example/security"},
            "recipient": {"person_id": "per-casey-lin", "first_name": "Casey"},
        }
    )
    page = RecordedResponse.model_validate(
        {
            "url": "https://kestrel.example/security",
            "status": "retrieved",
            "observed_at": "2026-10-18T00:00:00Z",
            "excerpt": "How we keep watch\nOur SIEM is Beaconlog, tuned in house.",
        }
    )
    printed: list[str] = []
    ask, _ = script(store, "fetch acc-kestrel https://kestrel.example/security", "stack")
    session = shell.Session(
        store=store,
        me="demo-seller",
        ask=ask,
        out=printed.append,
        acquisition=lambda: FixtureAcquisition([page]),
        live=False,
        prospects={"acc-kestrel": account},
        strategy=strategy,
    )
    shell.run(session)
    (line,) = [p for p in printed if p.startswith("Beaconlog named")]
    assert 'near "SIEM"' in line and "Our SIEM is Beaconlog" in line


def test_views_print_as_json_for_a_coding_agent() -> None:
    store, session = northstar()
    ask, _ = script(store, "research acc-northstar")
    shell.run(session(ask, lambda _: None))
    rows = json.loads(shell.as_json(accounts_view(store)))
    (row,) = rows
    assert row["qualification"]["outcome"] == "qualified"
    assert row["qualification"]["decided_at"].startswith("2026-10-14T")
    assert row["drafts"][0]["delivery"] == "not delivered"


def test_the_shell_refuses_input_that_does_not_come_from_a_terminal(
    capsys: pytest.CaptureFixture[str],
) -> None:
    scope = str(NORTHSTAR / "scope.json")
    argv = ["shell", scope, "--mongodb", "mongodb://127.0.0.1:1", "--me", "someone"]
    assert main(argv) == 1
    assert "must be used at a terminal" in capsys.readouterr().out


def test_questions_need_no_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "interactive", lambda: False)
    assert "accounts" in cli.QUERY_COMMANDS and "shell" not in cli.QUERY_COMMANDS
