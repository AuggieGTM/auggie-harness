"""Research that counts hiring from careers listings (ADR 0024), on the second seller's
synthetic scenario: the strategy's own vocabulary, replayed listing replies, no network."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from auggie import cli, shell
from auggie.acquisition.fixture import FixtureAcquisition, RecordedListing, RecordedListings
from auggie.cli import main
from auggie.contracts import CareersSnapshot, CitationError, OpenRole, roles_digest
from auggie.https import HttpResponse
from auggie.judgment import ListingReadings
from auggie.judgment.rules import RulesQualifier, assess
from auggie.operate import describe_draft
from auggie.pipeline import record_revision, record_snapshot, research_run
from auggie.projections import TABS, project
from auggie.queries import brief_view, hiring_row, hiring_view
from auggie.store import Backend, Store
from auggie.store.memory import InMemoryBackend
from auggie.strategy import LoadedStrategy, load_strategy
from auggie.synthetic import SyntheticInputs, load_inputs, run_scenario
from tests.conftest import EXAMPLES, MONGODB_URI, mongo_backend
from tests.test_careers_reader import Replay

KESTREL = EXAMPLES / "kestrel-security"
INPUTS = load_inputs(KESTREL)


@pytest.fixture(params=["memory", "mongodb"])
def backend(request: pytest.FixtureRequest) -> Iterator[Backend]:
    if request.param == "memory":
        yield InMemoryBackend()
    else:
        with mongo_backend() as mongo:
            yield mongo


def strategy() -> LoadedStrategy:
    return load_strategy(
        KESTREL / "inputs", INPUTS.strategy.path, commit=INPUTS.strategy.commit, scope=INPUTS.scope
    )


def findings(decision: Any) -> dict[str, Any]:
    return {f.criterion: f for f in decision.findings}


def with_current(status: int, body: Any) -> SyntheticInputs:
    """The scenario, with this run's read of the listing replaced."""
    earlier, current = INPUTS.listings
    replaced = current.model_copy(update={"status": status, "body": body})
    return INPUTS.model_copy(update={"listings": (earlier, replaced)})


# The scenario, end to end


def test_a_second_seller_qualifies_and_drafts_from_the_listing(backend: Backend) -> None:
    run = run_scenario(KESTREL, backend=backend)
    decision = run.result.decision
    assert decision.outcome == "qualified" and decision.unknowns == ()
    found = findings(decision)
    hiring = found["detection-hiring"]
    assert (hiring.assessment, hiring.basis) == ("meets", "inference")
    assert hiring.snapshot_ids == ("snap-fernhollow-001",) and hiring.evidence_ids == ()
    assert hiring.rationale.startswith(
        "detection engineering on the Lever listing read 2026-10-15: 2 open roles in the family."
    )
    assert "Senior Detection Engineer (https://jobs.lever.example/fernhollow/" in hiring.rationale
    new = found["detection-openings-new"]
    assert new.assessment == "meets"
    assert new.snapshot_ids == ("snap-fernhollow-001", "snap-fernhollow-before-1")
    assert "None before; 2 open now. Compared with the reading of 2026-09-10." in new.rationale
    assert found["names-beaconlog"].evidence_ids == ("ev-fernhollow-security",)

    draft = run.result.draft
    assert draft is not None
    assert (draft.evidence_ids, draft.snapshot_ids) == ((), ("snap-fernhollow-001",))
    assert run.delivery is not None and run.outcome is not None

    store = run.store
    read = store.get_careers_snapshot("snap-fernhollow-001")
    assert read.status == "complete" and len(read.roles) == 4
    assert read.acquired_via.id == "fixture-careers"
    recorded = store.list_events(type="careers.recorded")
    assert [(e.refs[0].id, e.run_id) for e in recorded] == [
        ("snap-fernhollow-before-1", None),
        ("snap-fernhollow-001", "run-001"),
    ]
    assert recorded[1].attributes == {"status": "complete", "roles": 4}
    (composed,) = store.list_events(type="draft.composed")
    assert ("careers", "snap-fernhollow-001") in {(r.kind, r.id) for r in composed.refs}


def test_the_reviewer_sees_the_listing_a_draft_rests_on() -> None:
    run = run_scenario(KESTREL)
    assert run.result.draft is not None
    printed: list[str] = []
    describe_draft(run.store, run.result.draft, printed.append)
    assert (
        "          snap-fernhollow-001: https://jobs.lever.example/fernhollow "
        "(read 2026-10-15 09:00:03+00:00, complete, 4 roles)"
    ) in printed


def test_the_brief_and_the_workbook_name_the_listing_a_finding_counted() -> None:
    run = run_scenario(KESTREL)
    brief = shell.render_brief(brief_view(run.store, "acc-fernhollow"))
    assert any(
        "detection-hiring (inference)" in line and line.endswith("[snap-fernhollow-001]")
        for line in brief
    )
    rows = {row[2]: row for row in project(run.store)[TABS[1]][1:]}
    assert rows["detection-openings-new"][6:] == (
        "snap-fernhollow-001; snap-fernhollow-before-1",
        "https://jobs.lever.example/fernhollow; https://jobs.lever.example/fernhollow",
    )


def test_a_revision_keeps_the_snapshots_its_draft_rests_on() -> None:
    undelivered = INPUTS.model_copy(update={"delivery": None, "outcome": None})
    run = run_scenario(KESTREL, inputs=undelivered)
    assert run.result.draft is not None
    revised = record_revision(
        run.store,
        previous=run.result.draft.ref,
        content=run.result.draft.content.replace("Riley", "Riley Moss"),
        by=INPUTS.reviews[0].reviewer,
        at=datetime(2026, 10, 17, tzinfo=UTC),
    )
    assert revised.snapshot_ids == ("snap-fernhollow-001",)


# When the listing settles nothing


def research(inputs: SyntheticInputs, *, read: bool = True) -> tuple[Store, Any]:
    store = InMemoryBackend().open(inputs.scope)
    *earlier, current = inputs.listings
    for n, reply in enumerate(earlier, start=1):
        record_snapshot(
            store,
            reader=RecordedListings([reply]),
            account=inputs.account,
            snapshot_id=f"snap-before-{n}",
            run_id=None,
        )
    result = research_run(
        store,
        strategy=strategy(),
        account=inputs.account,
        acquisition=FixtureAcquisition(inputs.responses),
        qualifier=RulesQualifier(),
        times=inputs.run,
        listings=RecordedListings([current]) if read else None,
    )
    return store, result


def test_without_a_reader_hiring_criteria_stay_unknown() -> None:
    store, result = research(INPUTS, read=False)
    hiring = findings(result.decision)["detection-hiring"]
    assert hiring.assessment == "unknown" and hiring.snapshot_ids == ()
    assert hiring.rationale == "No careers listing was read for this account."
    assert result.decision.outcome == "needs_review" and result.draft is None
    assert [s.snapshot_id for s in store.list_careers_snapshots("acc-fernhollow")] == [
        "snap-before-1"
    ]


def test_a_failed_read_settles_nothing_and_no_older_snapshot_stands_in() -> None:
    store, result = research(with_current(503, "unavailable"))
    found = findings(result.decision)
    for criterion in ("detection-hiring", "detection-openings-new"):
        finding = found[criterion]
        assert (finding.assessment, finding.basis, finding.snapshot_ids) == (
            "unknown",
            "hypothesis",
            (),
        )
        assert "The listing could not be read." in finding.rationale
        assert "The listing service answered HTTP 503." in finding.rationale
    assert result.decision.outcome == "needs_review" and result.draft is None
    assert result.snapshot is not None and result.snapshot.status == "failed"
    assert store.get_careers_snapshot("snap-fernhollow-001").status == "failed"


def test_a_listing_that_disagrees_with_itself_settles_nothing() -> None:
    current = INPUTS.listings[-1]
    assert isinstance(current.body, list) and isinstance(current.body[0], dict)
    retitled = {**current.body[0], "text": "Principal Detection Engineer"}
    _, result = research(with_current(200, [*current.body, retitled]))
    hiring = findings(result.decision)["detection-hiring"]
    assert hiring.assessment == "unknown" and hiring.snapshot_ids == ()
    assert "did not agree" in hiring.rationale


def test_research_refuses_a_citation_of_a_snapshot_that_is_not_the_baseline() -> None:
    inputs = INPUTS
    # An earlier read only 10 days before is too recent to be the baseline.
    too_recent = inputs.listings[0].model_copy(
        update={"read_at": datetime(2026, 10, 5, 9, 0, tzinfo=UTC)}
    )
    inputs = inputs.model_copy(update={"listings": (too_recent, inputs.listings[1])})

    class CitesTheEarlierRead(RulesQualifier):
        def qualify(self, **kwargs: Any) -> Any:
            decision = super().qualify(**kwargs)
            first = decision.findings[0].model_copy(
                update={"snapshot_ids": ("snap-fernhollow-001", "snap-before-1")}
            )
            return decision.model_copy(update={"findings": (first, *decision.findings[1:])})

    store = InMemoryBackend().open(inputs.scope)
    record_snapshot(
        store,
        reader=RecordedListings([too_recent]),
        account=inputs.account,
        snapshot_id="snap-before-1",
        run_id=None,
    )
    with pytest.raises(CitationError, match="snap-before-1 is neither this run's read"):
        research_run(
            store,
            strategy=strategy(),
            account=inputs.account,
            acquisition=FixtureAcquisition(inputs.responses),
            qualifier=CitesTheEarlierRead(),
            times=inputs.run,
            listings=RecordedListings([inputs.listings[1]]),
        )
    assert store.list_events(type="qualification.decided") == ()


# How a tally becomes a finding


def snap(snapshot_id: str, read_at: str, status: str, titles: list[str]) -> CareersSnapshot:
    roles = tuple(
        OpenRole.model_validate(
            {"role_id": f"{snapshot_id}-{n}", "title": t, "url": f"https://jobs.example/{n}"}
        )
        for n, t in enumerate(titles)
    )
    return CareersSnapshot.model_validate(
        {
            "snapshot_id": snapshot_id,
            "customer_id": "demo-security",
            "account_id": "acc-fernhollow",
            "listing": INPUTS.listings[0].listing.model_dump(mode="json"),
            "read_at": read_at,
            "status": status,
            "roles": [r.model_dump(mode="json") for r in roles],
            "content_hash": roles_digest(roles),
            "acquired_via": {"kind": "service", "id": "careers-reader", "version": "1"},
        }
    )


def judged(criterion: str, current: CareersSnapshot, *earlier: CareersSnapshot) -> Any:
    rules = strategy().strategy
    sources: Mapping[str, Any] = {}
    at = datetime(2026, 10, 15, 10, tzinfo=UTC)
    listing = ListingReadings(current, tuple(earlier))
    return assess(rules.criterion(criterion), rules, sources, at, listing)


DETECTION = ["Detection Engineer", "Senior Detection Engineer"]


def test_a_partial_read_can_meet_a_lower_bound() -> None:
    finding = judged("detection-hiring", snap("s1", "2026-10-15T09:00:00Z", "partial", DETECTION))
    assert (finding.assessment, finding.snapshot_ids) == ("meets", ("s1",))
    assert "at least 2 open roles" in finding.rationale


def test_a_complete_read_with_too_few_roles_does_not_meet() -> None:
    current = snap("s1", "2026-10-15T09:00:00Z", "complete", ["Detection Engineer", "SOC Analyst"])
    finding = judged("detection-hiring", current)
    assert (finding.assessment, finding.basis, finding.snapshot_ids) == (
        "does_not_meet",
        "inference",
        ("s1",),
    )


def test_new_openings_compare_with_the_baseline_only() -> None:
    current = snap("s2", "2026-10-15T09:00:00Z", "complete", DETECTION)
    had_some = snap("s1", "2026-09-10T09:00:00Z", "complete", ["Detection Engineer"])
    finding = judged("detection-openings-new", current, had_some)
    assert (finding.assessment, finding.snapshot_ids) == ("does_not_meet", ("s2", "s1"))
    assert "Compared with the reading of 2026-09-10." in finding.rationale

    too_recent = snap("s1", "2026-10-05T09:00:00Z", "complete", [])
    finding = judged("detection-openings-new", current, too_recent)
    assert (finding.assessment, finding.snapshot_ids) == ("unknown", ("s2",))
    assert "No complete earlier reading is in the comparison window." in finding.rationale


# The shell asks before a live read


def test_live_research_asks_before_reading_the_listing() -> None:
    store = InMemoryBackend().open(INPUTS.scope)
    asked: list[str] = []
    answers = iter(["research acc-fernhollow", "n"])

    def ask(prompt: str) -> str:
        asked.append(prompt)
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    session = shell.Session(
        store=store,
        me="demo-seller",
        ask=ask,
        out=lambda line: None,
        acquisition=lambda: FixtureAcquisition(INPUTS.responses),
        listings=lambda: RecordedListings([INPUTS.listings[1]]),
        live=True,
        prospects={INPUTS.account.account_id: INPUTS.account},
        strategy=strategy(),
    )
    shell.run(session)
    assert any(
        "1 pages through Treg, at most $0.002, and the careers listing on lever, free; go ahead?"
        in q
        for q in asked
    )
    assert store.list_careers_snapshots("acc-fernhollow") == ()


def test_a_replay_reads_each_listings_newest_recorded_reply() -> None:
    reader = RecordedListings(INPUTS.listings)
    listing = INPUTS.account.careers_listing
    assert listing is not None
    read = reader.read(
        listing=listing, customer_id="demo-security", account_id="acc-fernhollow", snapshot_id="s1"
    )
    assert read.read_at == INPUTS.listings[1].read_at and len(read.roles) == 4
    twice = RecordedListing.model_validate(INPUTS.listings[1].model_dump(mode="json"))
    with pytest.raises(ValueError, match="at one time"):
        RecordedListings([INPUTS.listings[1], twice])


# Asking who is hiring


def kestrel_session(
    store: Store, answers: list[str], *, live: bool = False
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

    session = shell.Session(
        store=store,
        me="demo-seller",
        ask=ask,
        out=printed.append,
        acquisition=lambda: FixtureAcquisition(INPUTS.responses),
        listings=lambda: RecordedListings([INPUTS.listings[1]]),
        live=live,
        prospects={INPUTS.account.account_id: INPUTS.account},
        strategy=strategy(),
    )
    return session, asked, printed


def test_hiring_shows_each_accounts_newest_read_counted_by_family() -> None:
    run = run_scenario(KESTREL)
    session, _, printed = kestrel_session(run.store, ["hiring", "hiring acc-elsewhere"])
    shell.run(session)
    assert printed[1:-1] == [
        "acc-fernhollow: Lever listing read 2026-10-15 (snap-fernhollow-001), complete: "
        "4 roles, 3 of them in your families",
        "    detection engineering: 2 (senior 1, unlevelled 1)",
        "      Senior Detection Engineer "
        "https://jobs.lever.example/fernhollow/6a1f0c2e-0000-4000-8000-000000000001",
        "      Detection Engineer "
        "https://jobs.lever.example/fernhollow/6a1f0c2e-0000-4000-8000-000000000002",
        "    security operations: 1 (unlevelled 1)",
        "      SOC Analyst II "
        "https://jobs.lever.example/fernhollow/6a1f0c2e-0000-4000-8000-000000000003",
        "    holds (inference): new detection openings in detection engineering: None before; "
        "2 open now. [snap-fernhollow-001, snap-fernhollow-before-1]",
        "      Senior Detection Engineer "
        "https://jobs.lever.example/fernhollow/6a1f0c2e-0000-4000-8000-000000000001",
        "      Detection Engineer "
        "https://jobs.lever.example/fernhollow/6a1f0c2e-0000-4000-8000-000000000002",
        "no listing read yet",
    ]
    (row,) = json.loads(shell.as_json(hiring_view(run.store, strategy().strategy)))
    assert row["basis"] == "inference" and row["read_at"] == "2026-10-15T09:00:03+00:00"


def external(allowed: bool) -> Any:
    execution = INPUTS.scope.execution.model_copy(update={"external_providers": allowed})
    return INPUTS.scope.model_copy(update={"execution": execution})


def test_reading_a_listing_from_the_shell_asks_first_when_live() -> None:
    store = InMemoryBackend().open(external(True))
    commands = ["hiring acc-fernhollow --read", "n", "hiring acc-fernhollow --read", "y"]
    session, asked, printed = kestrel_session(store, commands, live=True)
    shell.run(session)
    assert asked.count("the careers listing on lever, free; go ahead? [y/N] ") == 2
    assert "nothing read" in printed
    (read,) = store.list_careers_snapshots("acc-fernhollow")
    assert read.snapshot_id.startswith("snap-fernhollow-run-") and read.status == "complete"
    assert any(line.startswith(f"recorded  {read.snapshot_id} complete") for line in printed)
    assert any("4 roles, 3 of them in your families" in line for line in printed)


def test_a_scope_without_external_calls_is_refused_before_asking() -> None:
    store = InMemoryBackend().open(external(False))
    session, asked, printed = kestrel_session(store, ["hiring acc-fernhollow --read"], live=True)
    shell.run(session)
    assert [q for q in asked if "go ahead" in q] == []
    assert any("ExternalCallRefused" in line for line in printed)
    assert store.list_careers_snapshots("acc-fernhollow") == ()


def test_hiring_from_the_command_line_reads_on_request_and_prints_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    prospects = tmp_path / "prospects"
    prospects.mkdir()
    (prospects / "fernhollow.json").write_text((KESTREL / "prospect.json").read_text())
    scope = json.loads((KESTREL / "scope.json").read_text(encoding="utf-8"))
    scope["execution"]["external_providers"] = True
    scope_file = tmp_path / "scope.json"
    scope_file.write_text(json.dumps(scope), encoding="utf-8")
    body = json.dumps(INPUTS.listings[1].body).encode()
    listing = Replay(HttpResponse(200, {}, body))
    monkeypatch.setattr(cli, "listing_transport", lambda: listing)
    monkeypatch.setattr(cli, "interactive", lambda: False)
    with mongo_backend() as backend:
        common = [
            str(scope_file),
            "--mongodb",
            os.environ[MONGODB_URI],
            "--database-prefix",
            backend.database_prefix,
            "--prospects",
            str(prospects),
            "--strategy-repo",
            str(Path(__file__).resolve().parent.parent),
            "--strategy-path",
            "examples/synthetic/kestrel-security/inputs/strategy/security.toml",
        ]
        reading = ["hiring", *common, "--account", "acc-fernhollow", "--read"]
        assert main([*reading, "--json"]) == 1
        assert "JSON commands only read" in capsys.readouterr().out
        assert listing.requests == []
        assert main(reading) == 0
        out = capsys.readouterr().out
        assert "recorded  snap-fernhollow-run-" in out and "3 of them in your families" in out
        assert main(["hiring", *common, "--json"]) == 0
        (row,) = json.loads(capsys.readouterr().out)
        assert (row["account_id"], row["status"], row["relevant"]) == (
            "acc-fernhollow",
            "complete",
            3,
        )
        assert row["patterns"] == []  # one read, so no baseline yet
    [request] = listing.requests
    assert request.url.startswith("https://api.lever.co/v0/postings/fernhollow?")


# What Pi's review found


def research_with(store: Store, inputs: SyntheticInputs, qualifier: Any) -> Any:
    return research_run(
        store,
        strategy=strategy(),
        account=inputs.account,
        acquisition=FixtureAcquisition(inputs.responses),
        qualifier=qualifier,
        times=inputs.run,
        listings=RecordedListings([inputs.listings[-1]]),
    )


def test_no_qualifier_can_settle_hiring_from_an_older_read_when_this_runs_failed(
    backend: Backend,
) -> None:
    failed_now = with_current(503, "unavailable")
    store = backend.open(failed_now.scope)
    baseline = failed_now.listings[0].model_copy(
        update={"body": INPUTS.listings[1].body}  # two detection roles, read in September
    )
    record_snapshot(
        store,
        reader=RecordedListings([baseline]),
        account=failed_now.account,
        snapshot_id="snap-before-1",
        run_id=None,
    )

    class UsesTheOlderRead(RulesQualifier):
        """Judges as if the September read were this run's: the substitution ADR 0024 forbids."""

        def qualify(self, **kwargs: Any) -> Any:
            listing = kwargs["listing"]
            (older,) = listing.earlier
            kwargs["listing"] = ListingReadings(older, ())
            return super().qualify(**kwargs)

    with pytest.raises(CitationError, match="settled without citing a usable read"):
        research_with(store, failed_now, UsesTheOlderRead())
    assert store.list_events(type="qualification.decided") == ()
    assert store.list_events(type="draft.composed") == ()


@pytest.mark.parametrize(
    ("criterion", "snapshot_ids", "problem"),
    [
        ("names-beaconlog", ("snap-fernhollow-001",), "only a hiring criterion cites snapshots"),
        ("detection-hiring", ("snap-fernhollow-001", "snap-before-1"), "snap-before-1 is neither"),
        ("detection-hiring", ("snap-other-board",), "snap-other-board is neither"),
    ],
    ids=["page-finding", "baseline-for-a-count", "another-board"],
)
def test_research_refuses_snapshot_citations_outside_each_findings_own_reads(
    backend: Backend, criterion: str, snapshot_ids: tuple[str, ...], problem: str
) -> None:
    store = backend.open(INPUTS.scope)
    record_snapshot(
        store,
        reader=RecordedListings([INPUTS.listings[0]]),
        account=INPUTS.account,
        snapshot_id="snap-before-1",
        run_id=None,
    )
    other_board = INPUTS.listings[1].model_copy(
        update={
            "listing": INPUTS.listings[1].listing.model_copy(update={"board": "fernhollow-eu"}),
            "read_at": datetime(2000, 1, 1, tzinfo=UTC),
        }
    )
    elsewhere = INPUTS.account.model_copy(update={"careers_listing": other_board.listing})
    record_snapshot(
        store,
        reader=RecordedListings([other_board]),
        account=elsewhere,
        snapshot_id="snap-other-board",
        run_id=None,
    )

    class Cites(RulesQualifier):
        def qualify(self, **kwargs: Any) -> Any:
            decision = super().qualify(**kwargs)
            findings = tuple(
                f.model_copy(update={"snapshot_ids": snapshot_ids})
                if f.criterion == criterion
                else f
                for f in decision.findings
            )
            return decision.model_copy(update={"findings": findings})

    with pytest.raises(CitationError, match=problem):
        research_with(store, INPUTS, Cites())
    assert store.list_events(type="qualification.decided") == ()


def test_a_hiring_finding_cannot_be_settled_from_page_evidence() -> None:
    store = InMemoryBackend().open(INPUTS.scope)

    class FromThePage(RulesQualifier):
        def qualify(self, **kwargs: Any) -> Any:
            decision = super().qualify(**kwargs)
            hiring, *rest = decision.findings
            moved = hiring.model_copy(
                update={"snapshot_ids": (), "evidence_ids": ("ev-fernhollow-security",)}
            )
            return decision.model_copy(update={"findings": (moved, *rest)})

    with pytest.raises(CitationError, match="detection-hiring: settled without citing"):
        research_with(store, INPUTS, FromThePage())


def test_an_older_read_recorded_later_never_stands_in_for_a_newer_failure(
    backend: Backend,
) -> None:
    store = backend.open(INPUTS.scope)
    failed = INPUTS.listings[1].model_copy(
        update={"read_at": datetime(2026, 10, 18, 9, tzinfo=UTC), "status": 503, "body": ""}
    )
    for n, reply in enumerate((failed, INPUTS.listings[1], INPUTS.listings[0]), start=1):
        record_snapshot(
            store,
            reader=RecordedListings([reply]),
            account=INPUTS.account,
            snapshot_id=f"snap-{n}",
            run_id=None,
        )
    (row,) = hiring_view(store, strategy().strategy)
    assert (row["snapshot_id"], row["status"], row["families"]) == ("snap-1", "failed", [])
    # Of two read at the same moment, the one recorded later is the newer.
    same_time = INPUTS.listings[1].model_copy(update={"read_at": failed.read_at})
    record_snapshot(
        store,
        reader=RecordedListings([same_time]),
        account=INPUTS.account,
        snapshot_id="snap-4",
        run_id=None,
    )
    (row,) = hiring_view(store, strategy().strategy)
    assert (row["snapshot_id"], row["status"], row["relevant"]) == ("snap-4", "complete", 3)


def test_a_bad_id_stops_a_listing_read_before_it_is_made(backend: Backend) -> None:
    store = backend.open(INPUTS.scope)
    reads: list[str] = []

    class Counting(RecordedListings):
        def read(self, **kwargs: Any) -> CareersSnapshot:
            reads.append(kwargs["snapshot_id"])
            return super().read(**kwargs)

    for snapshot_id, run_id in (("snap-ok", "bad run id"), ("snap bad", "run-001")):
        with pytest.raises(ValueError, match="not a valid"):
            record_snapshot(
                store,
                reader=Counting([INPUTS.listings[1]]),
                account=INPUTS.account,
                snapshot_id=snapshot_id,
                run_id=run_id,
            )
    assert reads == []
    assert store.list_careers_snapshots("acc-fernhollow") == ()
    assert store.list_events() == ()


def test_counts_from_a_partial_read_are_shown_as_lower_bounds() -> None:
    current = snap("s1", "2026-10-15T09:00:00Z", "partial", [*DETECTION, "SOC Analyst"])
    hiring = strategy().strategy.signals.hiring
    row = hiring_row("acc-fernhollow", ListingReadings(current), hiring)
    lines = shell.render_hiring(row)
    assert lines[0].endswith("partial: at least 3 roles, at least 3 of them in your families")
    assert "    detection engineering: at least 2 (senior 1, unlevelled 1)" in lines
    assert row["status"] == "partial"  # the JSON says the counts are a lower bound too


def test_hiring_read_is_refused_whole_before_the_store_is_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pymongo import MongoClient

    prospects = tmp_path / "prospects"
    prospects.mkdir()
    (prospects / "fernhollow.json").write_text((KESTREL / "prospect.json").read_text())
    scope = json.loads((KESTREL / "scope.json").read_text(encoding="utf-8"))
    scope["execution"]["external_providers"] = True
    scope_file = tmp_path / "scope.json"
    scope_file.write_text(json.dumps(scope), encoding="utf-8")
    listing = Replay(HttpResponse(200, {}, b"[]"))
    monkeypatch.setattr(cli, "listing_transport", lambda: listing)
    repo = ["--strategy-repo", str(Path(__file__).resolve().parent.parent)]
    path = ["--strategy-path", "examples/synthetic/kestrel-security/inputs/strategy/security.toml"]
    with mongo_backend() as backend:
        common = [
            str(scope_file),
            "--mongodb",
            os.environ[MONGODB_URI],
            "--database-prefix",
            backend.database_prefix,
            "--prospects",
            str(prospects),
        ]
        reading = ["hiring", *common, "--account", "acc-fernhollow", "--read"]
        for extra, refusal in (
            ([*repo, *path, "--json"], "JSON commands only read"),
            ([], "hiring needs --strategy-repo and --strategy-path"),
            ([*repo, *path, "--account", "acc-elsewhere"], "no prospect file names"),
        ):
            assert main([*reading, *extra]) == 1
            assert refusal in capsys.readouterr().out
        client: MongoClient[dict[str, Any]] = MongoClient(os.environ[MONGODB_URI])
        try:
            names = client.list_database_names()
        finally:
            client.close()
        assert not [n for n in names if n.startswith(backend.database_prefix)]
    assert listing.requests == []
