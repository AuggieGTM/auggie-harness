"""Reading proof pages and checking proofs against them (ADR 0026), on the second seller's
synthetic strategy: recorded responses and a fake Treg, never the network."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from auggie import cli, shell
from auggie.acquisition.fixture import FixtureAcquisition, RecordedResponse
from auggie.acquisition.treg import TregPageText
from auggie.cli import main
from auggie.contracts import CustomerScope, SubjectRef
from auggie.credentials import CredentialFiles
from auggie.pipeline import read_proof_pages
from auggie.proofs import check_proofs, holds
from auggie.queries import accounts_view, find_view, proofs_view
from auggie.spend import RunBudget
from auggie.store import Store
from auggie.store.memory import InMemoryBackend
from auggie.strategy import LoadedStrategy, load_strategy
from auggie.synthetic import load_inputs, run_scenario
from tests.conftest import EXAMPLES, MONGODB_URI, mongo_backend
from tests.test_treg import TOKEN_VALUE, Replay, page

KESTREL = EXAMPLES / "kestrel-security"
INPUTS = load_inputs(KESTREL)
PAGE = "https://lanternwatch.example/customers/harborview-freight"
CLAIM = (
    "Harborview Freight cut the time to triage a detection alert from four hours to twenty minutes."
)
(RECORDED,) = [r for r in INPUTS.responses if str(r.url) == PAGE]
EXCERPT = RECORDED.excerpt or ""
RUN_AT = datetime(2026, 10, 15, 9, 5, tzinfo=UTC)


def strategy(**changes: Any) -> LoadedStrategy:
    loaded = load_strategy(
        KESTREL / "inputs", INPUTS.strategy.path, commit=INPUTS.strategy.commit, scope=INPUTS.scope
    )
    if not changes:
        return loaded
    data = loaded.strategy.model_dump(mode="json")
    data.update(changes)
    return LoadedStrategy(type(loaded.strategy).model_validate(data), loaded.ref)


def clock(moment: datetime) -> Callable[[], datetime]:
    return lambda: moment


def reads(store: Store, *responses: RecordedResponse, rules: LoadedStrategy | None = None) -> None:
    """Record one read of the proof page per response, in the order given."""
    for n, response in enumerate(responses):
        read_proof_pages(
            store,
            strategy=rules or strategy(),
            acquisition=FixtureAcquisition([response]),
            run_id=f"run-{n:03d}",
            now=clock(response.observed_at),  # each read is checked when it is made
        )


def recorded(**changes: Any) -> RecordedResponse:
    return RECORDED.model_copy(update=changes)


def check(store: Store, at: datetime = RUN_AT, rules: LoadedStrategy | None = None) -> Any:
    return check_proofs(store, (rules or strategy()).strategy, at)["harborview-triage"]


# Whether a draft may quote a proof


def test_the_scenario_records_the_proof_page_and_the_proof_is_usable() -> None:
    run = run_scenario(KESTREL)
    proof = check(run.store)
    assert proof.usable and proof.problem is None
    read = proof.read
    assert read is not None and read.evidence_id == "ev-proof-harborview-triage"
    assert read.subject == SubjectRef(kind="seller", id="demo-security")
    # The page breaks the claim over two lines; any whitespace matches one space.
    assert "four hours\nto twenty minutes" in (read.excerpt or "")
    validated = run.store.list_events(type="evidence.validated")
    assert any(r.id == read.evidence_id for e in validated for r in e.refs)


def test_seller_pages_are_never_shown_as_an_accounts_pages() -> None:
    run = run_scenario(KESTREL)
    assert [row["account_id"] for row in accounts_view(run.store)] == ["acc-fernhollow"]
    assert find_view(run.store, "Harborview") == []


@pytest.mark.parametrize(
    ("responses", "at", "problem"),
    [
        ((), RUN_AT, "its page has not been read"),
        (
            (
                recorded(),
                recorded(
                    observed_at=datetime(2026, 10, 14, tzinfo=UTC),
                    status="failed",
                    excerpt=None,
                    failure_reason="Timed out after 10 seconds.",
                ),
            ),
            RUN_AT,
            "the newest read of its page failed: Timed out after 10 seconds.",
        ),
        ((recorded(),), RECORDED.observed_at + timedelta(days=181), "more than 180 days before"),
        (
            (recorded(excerpt=EXCERPT.replace("Harborview Freight cut", "Harborview cut")),),
            RUN_AT,
            "does not hold the claim word for word",
        ),
        (
            (recorded(excerpt=EXCERPT.replace("twenty", "Twenty")),),
            RUN_AT,
            "does not hold the claim word for word",
        ),
        ((recorded(),), RECORDED.observed_at - timedelta(minutes=1), "has not been read"),
    ],
    ids=["never-read", "newest-read-failed", "too-old", "other-words", "other-case", "read-later"],
)
def test_a_proof_is_usable_only_while_its_newest_read_holds_the_claim(
    responses: tuple[RecordedResponse, ...], at: datetime, problem: str
) -> None:
    store = InMemoryBackend().open(INPUTS.scope)
    reads(store, *responses)
    proof = check(store, at)
    assert not proof.usable
    assert problem in (proof.problem or "")


def test_a_read_rejected_by_validation_cannot_support_a_proof() -> None:
    elsewhere = "https://not-reserved.zz/customers/harborview"
    rules = strategy(
        proofs={
            "max_age_days": 180,
            "by_id": {
                "harborview-triage": {
                    **strategy().strategy.proofs.by_id["harborview-triage"].model_dump(mode="json"),
                    "url": elsewhere,
                }
            },
        }
    )
    store = InMemoryBackend().open(INPUTS.scope)
    reads(store, recorded(url=elsewhere), rules=rules)
    proof = check(store, rules=rules)
    assert proof.problem == "the newest read of its page was rejected (non_reserved_domain)"


def test_holding_a_claim_keeps_case_and_word_edges() -> None:
    assert holds("It cut triage\n time  by half.", "cut triage time by half.")
    assert not holds("It cut Triage time by half.", "cut triage time by half.")
    assert not holds("It recut triage time by half.", "cut triage time by half.")


# Reading proof pages


def test_proofs_sharing_a_page_share_one_read_and_bad_ids_stop_before_reading() -> None:
    proof = strategy().strategy.proofs.by_id["harborview-triage"].model_dump(mode="json")
    twice = strategy(
        proofs={"max_age_days": 180, "by_id": {"harborview-triage": proof, "same-page": proof}}
    )
    store = InMemoryBackend().open(INPUTS.scope)
    verdicts = read_proof_pages(
        store,
        strategy=twice,
        acquisition=FixtureAcquisition([RECORDED]),
        run_id="run-001",
        now=lambda: RUN_AT,
    )
    assert [v.evidence.evidence_id for v in verdicts] == ["ev-proof-harborview-triage-run-001"]
    with pytest.raises(ValueError, match="not a valid run ID"):
        read_proof_pages(
            store,
            strategy=twice,
            acquisition=FixtureAcquisition([]),  # would raise if anything were read
            run_id="bad run",
            now=lambda: RUN_AT,
        )
    assert len(store.list_evidence()) == 1


def paid_scope() -> CustomerScope:
    execution = INPUTS.scope.execution.model_copy(
        update={"external_providers": True, "max_run_spend_micro_usd": 10_000}
    )
    return INPUTS.scope.model_copy(update={"execution": execution})


def credentials(root: Path, scope: CustomerScope) -> CredentialFiles:
    folder = root / scope.credential_namespace
    folder.mkdir(mode=0o700, parents=True)
    (folder / "treg-token").write_text(TOKEN_VALUE, encoding="utf-8")
    (folder / "treg-token").chmod(0o600)
    return CredentialFiles(root)


def test_a_proof_page_read_through_treg_is_paid_and_recorded(tmp_path: Path) -> None:
    scope = paid_scope()
    store = InMemoryBackend().open(scope)
    budget = RunBudget.for_scope(scope)
    treg = Replay(page(PAGE, RECORDED.excerpt or ""))
    acquisition = TregPageText(
        scope=scope,
        credentials=credentials(tmp_path, scope),
        budget=budget,
        transport=treg,
        now=lambda: RUN_AT,
    )
    (verdict,) = read_proof_pages(
        store, strategy=strategy(), acquisition=acquisition, run_id="run-001", now=lambda: RUN_AT
    )
    assert verdict.admitted and budget.spent_micro_usd == 1000
    assert len(treg.requests) == 1
    assert check(store, RUN_AT + timedelta(minutes=1)).usable


# The shell and the command line


def session(
    store: Store, answers: list[str], *, live: bool
) -> tuple[shell.Session, list[str], list[str]]:
    asked: list[str] = []
    printed: list[str] = []
    pending = iter(answers)

    def ask(prompt: str) -> str:
        asked.append(prompt)
        try:
            return next(pending)
        except StopIteration:
            raise EOFError from None

    return (
        shell.Session(
            store=store,
            me="demo-seller",
            ask=ask,
            out=printed.append,
            acquisition=lambda: FixtureAcquisition([RECORDED]),
            live=live,
            strategy=strategy(),
            now=lambda: RUN_AT,
        ),
        asked,
        printed,
    )


def test_the_shell_lists_proofs_and_asks_before_a_live_read() -> None:
    store = InMemoryBackend().open(paid_scope())
    commands = ["proofs", "proofs --read", "n", "proofs --read", "y"]
    shell_session, asked, printed = session(store, commands, live=True)
    shell.run(shell_session)
    assert asked.count("1 pages through Treg, at most $0.002; go ahead? [y/N] ") == 2
    assert printed[1] == (
        "harborview-triage (Harborview Freight, result): not usable: its page has not been "
        "read; read it with proofs --read"
    )
    assert f'    "{CLAIM}"' in printed and "nothing read" in printed
    assert any(line.startswith("recorded  ev-proof-harborview-triage-run-") for line in printed)
    usable = (
        "harborview-triage (Harborview Freight, result): usable, a vendor claim read 2026-10-12"
    )
    (n,) = [i for i, line in enumerate(printed) if line.startswith(usable)]
    assert printed[n + 1 : n + 3] == [f'    "{CLAIM}"', f"    {PAGE}"]
    assert printed[n + 3].startswith("    caveat: One customer's own measurement")


def test_a_scope_without_external_calls_is_refused_before_asking() -> None:
    store = InMemoryBackend().open(INPUTS.scope)
    shell_session, asked, printed = session(store, ["proofs --read"], live=True)
    shell.run(shell_session)
    assert [q for q in asked if "go ahead" in q] == []
    assert any("ExternalCallRefused" in line for line in printed)
    assert store.list_evidence() == ()


def test_the_proofs_view_is_a_vendor_claim_for_a_coding_agent() -> None:
    run = run_scenario(KESTREL)
    (row,) = json.loads(shell.as_json(proofs_view(run.store, strategy().strategy, RUN_AT)))
    assert (row["proof"], row["basis"], row["usable"]) == (
        "harborview-triage",
        "vendor_claim",
        True,
    )
    assert row["pains"] == ["alert-triage"] and row["claim"] == CLAIM
    assert row["read_at"] == "2026-10-12T10:00:00+00:00"


def test_proofs_from_the_command_line_read_on_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pymongo import MongoClient

    scope = paid_scope()
    scope_file = tmp_path / "scope.json"
    scope_file.write_text(scope.model_dump_json(), encoding="utf-8")
    credentials(tmp_path / "credentials", scope)
    treg = Replay(page(PAGE, RECORDED.excerpt or ""))
    monkeypatch.setattr(cli, "treg_transport", lambda: treg)
    monkeypatch.setattr(cli, "interactive", lambda: False)
    repo = ["--strategy-repo", str(Path(__file__).resolve().parent.parent)]
    path = ["--strategy-path", "examples/synthetic/kestrel-security/inputs/strategy/security.toml"]
    with mongo_backend() as backend:
        common = [
            str(scope_file),
            "--mongodb",
            os.environ[MONGODB_URI],
            "--database-prefix",
            backend.database_prefix,
            "--credentials",
            str(tmp_path / "credentials"),
        ]
        for extra, refusal in (
            ([*repo, *path, "--json"], "JSON commands only read"),
            ([], "proofs needs --strategy-repo and --strategy-path"),
        ):
            assert main(["proofs", *common, "--read", *extra]) == 1
            assert refusal in capsys.readouterr().out
        client: MongoClient[dict[str, Any]] = MongoClient(os.environ[MONGODB_URI])
        try:
            assert not [
                n for n in client.list_database_names() if n.startswith(backend.database_prefix)
            ]
        finally:
            client.close()
        assert treg.requests == []
        assert main(["proofs", *common, *repo, *path, "--read"]) == 0
        out = capsys.readouterr().out
        assert "reading 1 proof pages through Treg, run limit 10000 micro-USD" in out
        assert "recorded  ev-proof-harborview-triage-run-" in out
        assert "spent     1000 of 10000 micro-USD" in out
        assert main(["proofs", *common, *repo, *path, "--json"]) == 0
        (row,) = json.loads(capsys.readouterr().out)
        assert row["usable"] is True
    assert len(treg.requests) == 1
