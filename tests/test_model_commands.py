"""Model drafts from the commands and the shell (ADR 0031): a rehearsal from recorded replies,
a live list against a fake OpenRouter and a fake Treg, what review shows, and what learning
counts. Nothing leaves this machine."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from auggie import cli, operate, shell
from auggie.compose.fixture import RecordedModel, RecordedReplies
from auggie.compose.openrouter import OpenRouterWriter
from auggie.contracts import Actor, CustomerScope
from auggie.credentials import CredentialError, CredentialFiles
from auggie.https import HttpRequest, HttpResponse
from auggie.learning import learn_view
from auggie.spend import RunBudget
from auggie.store.memory import InMemoryBackend
from auggie.strategy import load_strategy
from tests.conftest import EXAMPLES, MONGODB_URI, mongo_backend
from tests.test_openrouter import Fake as FakeOpenRouter
from tests.test_openrouter import completion, endpoint, listing
from tests.test_research_list import (
    REPLAY,
    SCOPE,
    folder_of_prospects,
    paid,
    prospects,
    rehearse,
)
from tests.test_treg import Replay, page

KESTREL = EXAMPLES / "kestrel-security"
R2 = KESTREL / "inputs" / "strategy" / "security-r2.toml"
MODEL = "synthetic-lab/writer-1"
RECORDED = REPLAY["model_replies"][0]["texts"][0]
RULES = load_strategy(KESTREL / "inputs", "strategy/security-r2.toml", commit="0" * 40, scope=SCOPE)


def strategy_repo(tmp_path: Path) -> list[str]:
    """Revision 2 committed in a repository of its own, as a seller's strategy repo is."""
    repo = tmp_path / "strategy-repo"
    (repo / "strategy").mkdir(parents=True)
    (repo / "strategy" / "security.toml").write_text(R2.read_text(encoding="utf-8"))
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "strategy"], check=True)
    subprocess.run([*git, "commit", "-qm", "r2"], check=True)
    return ["--strategy-repo", str(repo), "--strategy-path", "strategy/security.toml"]


def live_scope(tmp_path: Path, key: bool = True) -> tuple[Path, Path]:
    scope = SCOPE.model_copy(
        update={
            "execution": SCOPE.execution.model_copy(
                update={"external_providers": True, "max_run_spend_micro_usd": 1_000_000}
            )
        }
    )
    path = tmp_path / "scope.json"
    path.write_text(scope.model_dump_json(), encoding="utf-8")
    root = tmp_path / "credentials"
    folder = root / scope.credential_namespace
    folder.mkdir(mode=0o700, parents=True)
    for name, value in (("treg-token", "synthetic-treg-token-0000"), ("openrouter-key", "sk-x")):
        if name == "openrouter-key" and not key:
            continue
        (folder / name).write_text(value, encoding="utf-8")
        (folder / name).chmod(0o600)
    return path, root


# Rehearsal


def test_revision_two_turns_model_drafts_on_for_both_plays() -> None:
    rules = RULES.strategy
    assert rules.writer is not None and rules.writer.model == MODEL
    assert [p.writer for p in rules.plays] == ["model", "model"]
    assert operate.model_calls(RULES) == 2


def test_a_list_rehearses_model_drafts_from_recorded_replies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = folder_of_prospects(tmp_path)
    with mongo_backend() as backend:
        argv = [
            "research-list",
            str(KESTREL / "scope.json"),
            "--prospects",
            str(folder),
            *strategy_repo(tmp_path),
            "--operator",
            "demo-seller",
            "--replay",
            str(KESTREL / "list-replay.json"),
            "--mongodb",
            os.environ[MONGODB_URI],
            "--database-prefix",
            backend.database_prefix,
        ]
        assert cli.main(argv) == 0
        out = capsys.readouterr().out
        assert "OpenRouter" not in out  # a rehearsal calls nothing
        assert "1 draft to review" in out
        store = backend.open_existing(SCOPE)
        (draft_id,) = operate.waiting_drafts(store)
        draft = store.latest_artifact(draft_id)
    assert draft.content == RECORDED
    assert draft.composed_by == Actor(kind="model", id=f"recorded {MODEL}")


def test_an_account_with_no_recorded_reply_keeps_its_template() -> None:
    store = InMemoryBackend().open(SCOPE)
    lines = rehearse(store, strategy=RULES, writer=RecordedModel(MODEL, []))
    draft = lines[0].result.draft
    assert draft.composed_by.kind == "service"
    assert draft.fallback == (
        "the model wrote nothing usable: No reply was recorded for this account."
    )


# Live, against fakes


def test_a_live_list_says_what_drafting_can_cost_and_drafts_through_openrouter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scope_file, credentials = live_scope(tmp_path)
    pages = {r["url"]: page(r["url"], r.get("excerpt") or "a page") for r in REPLAY["responses"]}
    treg = Replay(pages)
    model = FakeOpenRouter(listing(endpoint()), completion(RECORDED))
    monkeypatch.setattr(cli, "treg_transport", lambda: treg)
    monkeypatch.setattr(cli, "openrouter_transport", lambda: model)
    monkeypatch.setattr(cli, "listing_transport", Listings)
    folder = folder_of_prospects(tmp_path)
    with mongo_backend() as backend:
        argv = [
            "research-list",
            str(scope_file),
            "--prospects",
            str(folder),
            *strategy_repo(tmp_path),
            "--operator",
            "demo-seller",
            "--credentials",
            str(credentials),
            "--mongodb",
            os.environ[MONGODB_URI],
            "--database-prefix",
            backend.database_prefix,
        ]
        assert cli.main(argv) == 0
        out = capsys.readouterr().out
        assert f"and drafts through OpenRouter: up to 2 calls to {MODEL} for each, at most" in out
        store = backend.open_existing(SCOPE)
        waiting = operate.waiting_drafts(store)
        assert len(waiting) == 1, out
        draft = store.latest_artifact(waiting[0])
        (called,) = store.list_events(type="model.called")
    assert draft.content == RECORDED and draft.composed_by == Actor(kind="model", id=MODEL)
    assert called.attributes["result"] == "accepted" and called.attributes["cost_micro_usd"] == 7200
    # Prices are read for the header, for the list's worst case, and before the one call.
    assert [r.method for r in model.requests] == ["GET", "GET", "GET", "POST"]


class Listings:
    """Answers each listing service's request with the recorded reply for its board."""

    def send(self, request: HttpRequest) -> HttpResponse:
        for recorded in REPLAY["listings"]:
            if f"/{recorded['listing']['board']}" in request.url:
                body = json.dumps(recorded["body"]).encode()
                return HttpResponse(recorded["status"], {"content-type": "application/json"}, body)
        raise OSError("no such board")


def test_a_live_list_without_a_key_stops_before_the_store_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scope_file, credentials = live_scope(tmp_path, key=False)
    treg = Replay(page("https://fernhollow.example/security", "x"))
    model = FakeOpenRouter()
    monkeypatch.setattr(cli, "treg_transport", lambda: treg)
    monkeypatch.setattr(cli, "openrouter_transport", lambda: model)
    argv = [
        "research-list",
        str(scope_file),
        "--prospects",
        str(folder_of_prospects(tmp_path)),
        *strategy_repo(tmp_path),
        "--operator",
        "demo-seller",
        "--credentials",
        str(credentials),
        "--mongodb",
        "mongodb://127.0.0.1:1",  # never reached
    ]
    assert cli.main(argv) == 1
    out = capsys.readouterr().out
    assert "stopped   CredentialError" in out and "'openrouter-key': not found" in out
    assert treg.requests == [] and model.requests == []


# The shell


def session(tmp_path: Path, answers: list[str], key: bool = True) -> tuple[Any, list[str]]:
    scope, _, acquisition, _ = paid(10_000, tmp_path)
    if key:
        folder = tmp_path / "credentials" / scope.credential_namespace
        (folder / "openrouter-key").write_text("sk-x", encoding="utf-8")
        (folder / "openrouter-key").chmod(0o600)
    asked: list[str] = []
    pending = iter(answers)

    def ask(prompt: str) -> str:
        asked.append(prompt)
        try:
            return next(pending)
        except StopIteration:
            raise EOFError from None

    def writer(budget: RunBudget | None) -> Any:
        assert budget is not None
        return OpenRouterWriter(
            scope=scope,
            credentials=CredentialFiles(tmp_path / "credentials"),
            budget=budget,
            writer=RULES.strategy.writer,  # type: ignore[arg-type]
            transport=FakeOpenRouter(listing(endpoint()), completion(RECORDED)),
        )

    run = shell.Session(
        store=InMemoryBackend().open(scope),
        me="demo-seller",
        ask=ask,
        out=lambda line: None,
        acquisition=lambda: acquisition,
        live=True,
        prospects={p.account_id: p for p in prospects(scope)},
        strategy=RULES,
        writer=writer,
    )
    return run, asked


def test_the_shell_says_drafts_go_through_openrouter_before_its_paid_question(
    tmp_path: Path,
) -> None:
    run, asked = session(tmp_path, ["research acc-fernhollow", "n"])
    shell.run(run)
    question = next(q for q in asked if "go ahead" in q)
    assert question == (
        "1 pages through Treg, at most $0.002, and the careers listing on lever, free, and the "
        "draft through OpenRouter: up to 2 "
        f"calls to {MODEL} at its listed price, under this command's limit of $0.010; "
        "go ahead? [y/N] "
    )


def test_the_shell_refuses_a_model_without_its_key_before_asking(tmp_path: Path) -> None:
    run, asked = session(tmp_path, ["research acc-fernhollow"], key=False)
    printed: list[str] = []
    run.out = printed.append
    shell.run(run)
    assert [q for q in asked if "go ahead" in q] == []
    assert any(line.startswith("stopped   CredentialError") for line in printed)


# Review and learning


def test_review_says_which_model_wrote_a_draft_or_why_the_template_stands() -> None:
    store = InMemoryBackend().open(SCOPE)
    replies = [RecordedReplies(account="Fernhollow Logistics", texts=(RECORDED,))]
    lines = rehearse(store, strategy=RULES, writer=RecordedModel(MODEL, replies))
    shown: list[str] = []
    operate.describe_draft(store, lines[0].result.draft, shown.append)
    assert any(f"written by the model recorded {MODEL}, and checked" in s for s in shown)
    other = InMemoryBackend().open(SCOPE)
    lines = rehearse(other, strategy=RULES, writer=RecordedModel(MODEL, []))
    shown = []
    operate.describe_draft(other, lines[0].result.draft, shown.append)
    assert any(
        "from the play's template, not the model's draft: the model wrote" in s for s in shown
    )


def test_learning_counts_a_plays_model_and_template_drafts_apart() -> None:
    from auggie.operate import deliver_draft, report_outcome, review_draft

    store = InMemoryBackend().open(SCOPE)
    replies = [RecordedReplies(account="Fernhollow Logistics", texts=(RECORDED,))]
    lines = rehearse(store, strategy=RULES, writer=RecordedModel(MODEL, replies))
    draft = lines[0].result.draft
    answers = iter(["y", "", "y"])
    review_draft(
        store,
        draft_id=draft.ref.artifact_id,
        reviewer="demo-seller",
        ask=lambda _: next(answers),
        out=lambda _: None,
    )
    deliver_draft(store, draft_id=draft.ref.artifact_id)
    report_outcome(
        store, draft_id=draft.ref.artifact_id, outcome="positive_reply", reporter="demo-seller"
    )
    (view,) = learn_view(store)["strategies"]
    assert [row["key"] for row in view["writers"]] == ["detection-build-out, by model"]
    assert view["writers"][0]["positive"] == 1


def test_a_key_is_never_printed_or_kept(tmp_path: Path) -> None:
    scope = CustomerScope.model_validate_json(live_scope(tmp_path)[0].read_text())
    writer = OpenRouterWriter(
        scope=scope,
        credentials=CredentialFiles(tmp_path / "credentials"),
        budget=RunBudget.for_scope(scope),
        writer=RULES.strategy.writer,  # type: ignore[arg-type]
        transport=FakeOpenRouter(),
    )
    writer.ready()
    other = CredentialFiles(tmp_path / "elsewhere")
    with pytest.raises(CredentialError) as refused:
        OpenRouterWriter(
            scope=scope,
            credentials=other,
            budget=RunBudget.for_scope(scope),
            writer=RULES.strategy.writer,  # type: ignore[arg-type]
        ).ready()
    assert "sk-x" not in str(refused.value) and "sk-x" not in json.dumps(REPLAY)
