"""Store projections expose lineage and statuses, never page text or draft wording."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from auggie.contracts import Artifact, HumanApproval, OperationalEvent, sha256_digest
from auggie.projections import TABS, Workbook, project
from auggie.projections.csv import write_csv
from auggie.store.memory import InMemoryBackend
from auggie.synthetic import ScenarioRun, run_scenario
from tests.conftest import NORTHSTAR, variant

HEADERS = (
    (
        "account_id",
        "outcome",
        "decided_at",
        "decision_id",
        "strategy",
        "criteria_met",
        "unknowns",
        "decided_by",
    ),
    (
        "account_id",
        "decision_id",
        "criterion",
        "assessment",
        "basis",
        "rationale",
        "evidence_ids",
        "source_urls",
    ),
    (
        "evidence_id",
        "subject",
        "source_url",
        "status",
        "observed_at",
        "published_at",
        "validation",
        "failure_reason",
        "provider_request_id",
        "acquired_via",
    ),
    (
        "artifact_id",
        "account_id",
        "recipient_id",
        "versions",
        "latest_version",
        "composed_at",
        "content_approvals",
        "delivery_approvals",
        "delivered_at",
        "outcome",
    ),
    (
        "account_id",
        "snapshot_id",
        "read_at",
        "listing",
        "status",
        "roles",
        "relevant_roles",
        "roles_per_family",
        "patterns",
        "links",
    ),
    (
        "strategy",
        "kind",
        "key",
        "delivered",
        "positive",
        "negative",
        "opted_out",
        "bounced",
        "waiting",
        "settled",
        "rate",
    ),
)
CAREERS = "https://northstar.example/careers/it-operations-manager"
NEWS = "https://news.example/2026/northstar-works-opens-second-office"


@pytest.fixture
def run() -> ScenarioRun:
    return run_scenario(NORTHSTAR)


def test_accounts_and_drafts_show_the_complete_scenario_rows(run: ScenarioRun) -> None:
    workbook = project(run.store)
    assert workbook[TABS[0]] == (
        HEADERS[0],
        (
            "acc-northstar",
            "qualified",
            "2026-10-14T16:05:00+00:00",
            "qual-northstar-001",
            "strategy/msp.toml@0000000",
            "headcount-50-to-500; active-it-hiring; device-management-change",
            "Has the account opened a new office in the last year?",
            "fixture-icp-rules 0",
        ),
    )
    assert workbook[TABS[3]] == (
        HEADERS[3],
        (
            "draft-northstar-001",
            "acc-northstar",
            "per-jordan-avery",
            "1",
            "1c05fafeae99",
            "2026-10-14T16:10:00+00:00",
            "approved by demo-reviewer at 2026-10-15T15:00:00+00:00",
            "approved by demo-reviewer at 2026-10-15T15:02:00+00:00 "
            "for simulated:per-jordan-avery until 2026-10-22T15:02:00+00:00",
            "2026-10-15T15:05:00+00:00",
            "positive_reply",
        ),
    )


def test_findings_and_evidence_distinguish_failed_retrievals(run: ScenarioRun) -> None:
    workbook = project(run.store)
    assert workbook[TABS[1]][1:3] == (
        (
            "acc-northstar",
            "qual-northstar-001",
            "headcount-50-to-500",
            "meets",
            "source_fact",
            "The careers page states 140 staff.",
            "ev-northstar-careers",
            CAREERS,
        ),
        (
            "acc-northstar",
            "qual-northstar-001",
            "active-it-hiring",
            "meets",
            "source_fact",
            "An open IT Operations Manager role, published within the last 30 days.",
            "ev-northstar-careers",
            CAREERS,
        ),
    )
    assert workbook[TABS[1]][4] == (
        "acc-northstar",
        "qual-northstar-001",
        "recent-expansion",
        "unknown",
        "hypothesis",
        "The news source could not be retrieved (failed).",
        "",
        "",
    )
    assert workbook[TABS[2]][1] == (
        "ev-northstar-careers",
        "account:acc-northstar",
        CAREERS,
        "retrieved",
        "2026-10-14T16:00:00+00:00",
        "2026-10-09T00:00:00+00:00",
        "admitted",
        "",
        "",
        "fixture-acquisition",
    )
    assert workbook[TABS[2]][3] == (
        "ev-northstar-news",
        "account:acc-northstar",
        NEWS,
        "failed",
        "2026-10-14T16:00:10+00:00",
        "",
        "rejected: retrieval_failed",
        "Timed out after 10 seconds.",
        "",
        "fixture-acquisition",
    )


def test_every_tab_has_its_header_and_only_string_cells(run: ScenarioRun) -> None:
    workbook = project(run.store)
    assert tuple(workbook) == TABS
    for tab, header in zip(TABS, HEADERS, strict=True):
        assert workbook[tab][0] == header
        assert all(len(row) == len(header) for row in workbook[tab])
        assert all(isinstance(cell, str) for row in workbook[tab] for cell in row)


def test_an_empty_store_has_only_headers(run: ScenarioRun) -> None:
    empty = InMemoryBackend().open(run.store.scope)
    assert project(empty) == dict(zip(TABS, ((header,) for header in HEADERS), strict=True))


def test_projection_is_repeatable_and_keeps_source_and_draft_text_out(run: ScenarioRun) -> None:
    events = run.store.list_events()
    workbook = project(run.store)
    assert project(run.store) == workbook
    assert run.store.list_events() == events
    assert run.result.draft is not None
    private = [e.excerpt for e in run.store.list_evidence() if e.excerpt]
    private.extend(line for line in run.result.draft.content.splitlines() if line.strip())
    assert all(
        text not in cell
        for rows in workbook.values()
        for row in rows
        for cell in row
        for text in private
    )


def test_latest_versions_exclude_older_approvals_and_keep_write_order(run: ScenarioRun) -> None:
    original = run.result.draft
    assert original is not None
    text = "An invented replacement draft."
    latest = Artifact.model_validate(
        variant(
            original,
            content=text,
            ref=variant(original.ref, content_hash=sha256_digest(text)),
            supersedes=original.ref.content_hash,
            composed_at="2026-10-17T12:00:00Z",
        )
    )
    run.store.put_artifact(latest)
    event = run.store.list_events(type="draft.composed")[0]
    run.store.append_event(
        OperationalEvent.model_validate(
            variant(
                event,
                event_id="evt-revision",
                idempotency_key="draft-revision",
                occurred_at="2026-10-17T12:00:00Z",
            )
        )
    )
    assert project(run.store)[TABS[3]][1][3:8] == (
        "2",
        latest.ref.content_hash[7:19],
        "2026-10-17T12:00:00+00:00",
        "",
        "",
    )
    for index, decision in enumerate(("changes_requested", "approved")):
        approval = HumanApproval.model_validate(
            variant(
                run.approvals[0],
                approval_id=f"apr-revision-{index}",
                artifact=latest.ref.model_dump(mode="json"),
                decision=decision,
                decided_at="2026-10-17T13:00:00Z",
            )
        )
        run.store.put_approval(approval)
    row = project(run.store)[TABS[3]][1]
    assert row[6] == (
        "changes_requested by demo-reviewer at 2026-10-17T13:00:00+00:00; "
        "approved by demo-reviewer at 2026-10-17T13:00:00+00:00"
    )
    assert row[7] == ""
    assert len(project(run.store)[TABS[3]]) == 2


def test_latest_outcome_follows_write_order_not_timestamp(run: ScenarioRun) -> None:
    event = run.store.list_events(type="outcome.recorded")[0]
    run.store.append_event(
        OperationalEvent.model_validate(
            variant(
                event,
                event_id="evt-later-outcome",
                idempotency_key="later-outcome",
                occurred_at="2026-10-16T17:00:00Z",
                attributes={"outcome": "negative_reply"},
            )
        )
    )
    assert project(run.store)[TABS[3]][1][-1] == "negative_reply"


def test_evidence_without_validation_has_a_blank_cell(run: ScenarioRun) -> None:
    store = InMemoryBackend().open(run.store.scope)
    store.put_evidence(run.store.list_evidence()[0])
    assert project(store)[TABS[2]][1][6] == ""


def read_csv(path: Path) -> tuple[tuple[str, ...], ...]:
    with path.open(encoding="utf-8", newline="") as stream:
        return tuple(tuple(row) for row in csv.reader(stream))


def test_csv_files_round_trip_in_tab_order_without_writing_elsewhere(
    run: ScenarioRun, tmp_path: Path
) -> None:
    directory = tmp_path / "export"
    workbook = project(run.store)
    paths = write_csv(workbook, directory)
    assert [p.name for p in paths] == [
        "accounts.csv",
        "findings.csv",
        "evidence.csv",
        "drafts.csv",
        "hiring.csv",
        "learning.csv",
    ]
    assert list(tmp_path.iterdir()) == [directory]
    assert set(directory.iterdir()) == set(paths)
    assert [read_csv(path) for path in paths] == list(workbook.values())
    assert write_csv(workbook, directory) == paths


def test_csv_formula_cells_are_guarded_and_text_stays_intact(tmp_path: Path) -> None:
    values = ("=1+1", "+x", "-x", "@x", "\tx", "\rx", "", "plain", 'café, with\nquotes: "yes"')
    workbook: Workbook = {tab: (("header",), values) for tab in TABS}
    paths = write_csv(workbook, tmp_path / "export")
    expected = tuple("'" + value for value in values[:6]) + values[6:]
    for path in paths:
        assert read_csv(path) == (("header",), expected)


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_existing_file_links_cannot_overwrite_files_outside_the_directory(
    tmp_path: Path, link: str
) -> None:
    outside = tmp_path / "untouched.csv"
    outside.write_text("keep me", encoding="utf-8")
    directory = tmp_path / "export"
    directory.mkdir()
    path = directory / "accounts.csv"
    if link == "symlink":
        path.symlink_to(outside)
    else:
        path.hardlink_to(outside)
    workbook: Workbook = {tab: (("header",),) for tab in TABS}
    write_csv(workbook, directory)
    assert outside.read_text(encoding="utf-8") == "keep me"
    assert read_csv(path) == (("header",),)


def test_extra_tab_names_cannot_create_files_outside_the_directory(tmp_path: Path) -> None:
    workbook = {tab: (("header",),) for tab in TABS}
    workbook["auggie: ../escape"] = (("not exported",),)
    paths = write_csv(workbook, tmp_path / "export")
    assert len(paths) == len(TABS)
    assert not (tmp_path / "escape.csv").exists()


def test_the_hiring_tab_counts_each_accounts_newest_read_by_the_sellers_families() -> None:
    from auggie.contracts import CareersSnapshot, roles_digest

    kestrel = run_scenario(NORTHSTAR.parent / "kestrel-security")
    strategy = kestrel.strategy.strategy
    _, row = project(kestrel.store, strategy)[TABS[4]]
    assert row == (
        "acc-fernhollow",
        "snap-fernhollow-001",
        "2026-10-15T09:00:03+00:00",
        "https://jobs.lever.example/fernhollow",
        "complete",
        "4",
        "3",
        "detection engineering 2 (senior 1, unlevelled 1); security operations 1 (unlevelled 1)",
        "new detection openings in detection engineering: None before; 2 open now. "
        "[snap-fernhollow-001, snap-fernhollow-before-1]",
        "https://jobs.lever.example/fernhollow/6a1f0c2e-0000-4000-8000-000000000001; "
        "https://jobs.lever.example/fernhollow/6a1f0c2e-0000-4000-8000-000000000002; "
        "https://jobs.lever.example/fernhollow/6a1f0c2e-0000-4000-8000-000000000003",
    )
    # Without the strategy there are no families to count by: only what the read was.
    _, bare = project(kestrel.store)[TABS[4]]
    assert bare[:6] == row[:6] and bare[6:] == ("", "", "", "")
    # A newer read that failed is shown as failed; the older one never stands in for it.
    failed = CareersSnapshot.model_validate(
        {
            **kestrel.store.get_careers_snapshot("snap-fernhollow-001").model_dump(mode="json"),
            "snapshot_id": "snap-fernhollow-002",
            "read_at": "2026-10-16T09:00:00Z",
            "status": "failed",
            "failure_reason": "The listing service answered HTTP 503.",
            "roles": [],
            "content_hash": roles_digest(()),
        }
    )
    kestrel.store.put_careers_snapshot(failed)
    _, newest = project(kestrel.store, strategy)[TABS[4]]
    assert newest[1] == "snap-fernhollow-002"
    assert newest[4:] == ("failed: The listing service answered HTTP 503.", "", "", "", "", "")


def test_the_learning_tab_counts_what_deliveries_led_to_by_strategy_file() -> None:
    kestrel = run_scenario(NORTHSTAR.parent / "kestrel-security")  # delivered; a meeting booked
    header, *rows = project(kestrel.store)[TABS[5]]
    assert header == HEADERS[5]
    by_key = {(row[1], row[2]): row for row in rows}
    assert by_key[("play", "detection-triage")] == (
        "strategy/security.toml",
        "play",
        "detection-triage",
        "1",
        "1",
        "0",
        "0",
        "0",
        "0",
        "1",
        "1.000",
    )
    assert ("pain", "alert-triage") in by_key and ("proof", "harborview-triage") in by_key
    assert ("criterion", "detection-hiring") in by_key
    # Model and template drafts are counted apart (ADR 0031); this one is the template's.
    assert (
        by_key[("writer", "detection-triage, by template")][3:]
        == by_key[("play", "detection-triage")][3:]
    )
    # No person's record or page text: the strategy's path and IDs as written, and counts.
    cells = " ".join(cell for row in rows for cell in row)
    assert "Casey" not in cells and "casey.lindqvist" not in cells
    assert "Harborview Freight" not in cells
