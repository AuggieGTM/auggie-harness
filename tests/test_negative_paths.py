"""The slice refuses failed sources, edited drafts, missing approvals and other scopes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from auggie.acquisition.fixture import FixtureAcquisition
from auggie.approval import ReviewDecision
from auggie.contracts import (
    Artifact,
    CitationError,
    CustomerScope,
    DeliveryTarget,
    ExecutionBoundary,
    HumanApproval,
    QualificationDecision,
    ScopeViolation,
    sha256_digest,
)
from auggie.delivery import DeliveryBlocked, authorize_delivery
from auggie.judgment.rules import RulesQualifier
from auggie.pipeline import (
    activate_strategy,
    deliver,
    record_outcome,
    record_review,
    research_run,
)
from auggie.store import RecordNotFound, Store
from auggie.store.memory import InMemoryBackend
from auggie.strategy import load_strategy
from auggie.synthetic import ScenarioRun, SyntheticInputs, load_inputs, run_scenario
from tests.conftest import NORTHSTAR, Scenario, variant

DELIVER_AT = datetime(2026, 10, 15, 15, 5, tzinfo=UTC)
REVIEWER: dict[str, str] = {"kind": "human", "id": "demo-reviewer"}


def inputs(**changes: Any) -> SyntheticInputs:
    return SyntheticInputs.model_validate(variant(load_inputs(NORTHSTAR), **changes))


def until_review(*reviews: dict[str, Any], backend: InMemoryBackend | None = None) -> ScenarioRun:
    """Run research and the given reviews; nothing is delivered."""
    chosen = inputs(reviews=list(reviews), delivery=None, outcome=None)
    return run_scenario(NORTHSTAR, inputs=chosen, backend=backend)


def review(scope: str, decision: str = "approved", **changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "scope": scope,
        "decision": decision,
        "reviewer": REVIEWER,
        "decided_at": "2026-10-15T15:00:00Z" if scope == "content" else "2026-10-15T15:02:00Z",
    }
    if scope == "delivery":
        data |= {"platform": "simulated", "expires_at": "2026-10-22T15:02:00Z"}
    return data | changes


def deliveries(store: Store) -> int:
    return len(store.list_events(type="delivery.simulated"))


def draft_of(run: ScenarioRun) -> Artifact:
    assert run.result.draft is not None
    return run.result.draft


# A failed source


def test_a_failed_source_is_recorded_rejected_and_never_cited() -> None:
    run = until_review()
    store = run.store
    news = store.get_evidence("ev-northstar-news")
    assert news.status == "failed" and news.excerpt is None
    rejected = store.list_events(type="evidence.rejected")
    assert [(e.refs[0].id, e.attributes) for e in rejected] == [
        ("ev-northstar-news", {"reason": "retrieval_failed"})
    ]
    assert "ev-northstar-news" not in run.result.decision.cited_evidence_ids
    assert "ev-northstar-news" not in draft_of(run).evidence_ids
    expansion = next(f for f in run.result.decision.findings if f.criterion == "recent-expansion")
    assert expansion.assessment == "unknown"


def test_a_decision_or_draft_citing_the_failed_source_is_refused(scenario: Scenario) -> None:
    store = until_review().store
    findings = [f.model_dump(mode="json") for f in scenario.qualification.findings]
    findings[3] |= {
        "assessment": "does_not_meet",
        "basis": "source_fact",
        "evidence_ids": ["ev-northstar-news"],
    }
    negative = variant(scenario.qualification, decision_id="qual-northstar-002", findings=findings)
    with pytest.raises(CitationError, match="ev-northstar-news: failed"):
        store.put_qualification(QualificationDecision.model_validate(negative))
    v1 = store.latest_artifact("draft-northstar-001")
    content = v1.content + "\nP.S. Congratulations on the new office.\n"
    citing = variant(
        v1,
        ref=variant(v1.ref, content_hash=sha256_digest(content)),
        content=content,
        supersedes=v1.ref.content_hash,
        evidence_ids=[*v1.evidence_ids, "ev-northstar-news"],
    )
    with pytest.raises(CitationError, match="ev-northstar-news: failed"):
        store.put_artifact(Artifact.model_validate(citing))


def test_when_every_source_fails_there_is_no_draft() -> None:
    failed = [
        {
            "url": str(r.url),
            "status": "failed",
            "observed_at": r.observed_at.isoformat(),
            "failure_reason": "Timed out after 10 seconds.",
        }
        for r in load_inputs(NORTHSTAR).responses
    ]
    run = run_scenario(NORTHSTAR, inputs=inputs(responses=failed))
    assert run.result.decision.outcome == "needs_review"
    assert run.result.draft is None
    assert run.result.stopped == "the account is needs_review"
    assert run.store.list_events(type="draft.composed") == ()
    assert run.approvals == () and run.delivery is None


# A draft edited after approval


def edit(store: Store, run: ScenarioRun) -> Artifact:
    v1 = draft_of(run)
    content = v1.content.replace("Would that be useful?", "Can I send it over today?")
    v2 = Artifact.model_validate(
        variant(
            v1,
            ref=variant(v1.ref, content_hash=sha256_digest(content)),
            content=content,
            supersedes=v1.ref.content_hash,
            composed_by=REVIEWER,
            composed_at="2026-10-15T15:03:00Z",
        )
    )
    store.put_artifact(v2)
    return v2


def test_an_edit_after_approval_blocks_delivery_of_both_versions() -> None:
    run = until_review(review("content"), review("delivery"))
    store = run.store
    v1 = draft_of(run)
    v2 = edit(store, run)
    with pytest.raises(DeliveryBlocked, match="no content approval of this version"):
        deliver(store, requested=v2.ref, platform="simulated", at=DELIVER_AT)
    with pytest.raises(DeliveryBlocked, match="superseded"):
        deliver(store, requested=v1.ref, platform="simulated", at=DELIVER_AT)
    assert deliveries(store) == 0


def test_the_edited_version_can_go_out_once_it_is_approved_itself() -> None:
    run = until_review(review("content"), review("delivery"))
    store = run.store
    v2 = edit(store, run)
    for scope in ("content", "delivery"):
        record_review(
            store,
            shown=v2.ref,
            review=ReviewDecision.model_validate(review(scope, decided_at="2026-10-15T15:04:00Z")),
            approval_id=f"apr-northstar-{scope}-v2",
        )
    assert deliver(store, requested=v2.ref, platform="simulated", at=DELIVER_AT).created


# Delivery without delivery approval


def test_content_approval_alone_does_not_deliver() -> None:
    run = until_review(review("content"))
    with pytest.raises(DeliveryBlocked, match="no delivery approval of this version"):
        deliver(run.store, requested=draft_of(run).ref, platform="simulated", at=DELIVER_AT)
    assert deliveries(run.store) == 0


def test_delivery_approval_alone_does_not_deliver() -> None:
    run = until_review(review("delivery"))
    with pytest.raises(DeliveryBlocked, match="no content approval"):
        deliver(run.store, requested=draft_of(run).ref, platform="simulated", at=DELIVER_AT)


@pytest.mark.parametrize(
    "at",
    [DELIVER_AT + timedelta(days=8), datetime(2026, 10, 15, 15, 1, tzinfo=UTC)],
    ids=["after-expiry", "before-approval"],
)
def test_a_delivery_approval_applies_only_while_in_force(at: datetime) -> None:
    run = until_review(review("content"), review("delivery"))
    with pytest.raises(DeliveryBlocked, match="no delivery approval"):
        deliver(run.store, requested=draft_of(run).ref, platform="simulated", at=at)


def test_a_rejection_of_the_version_blocks_delivery() -> None:
    rejected = review("content", "changes_requested", note="Too long.")
    rejected["decided_at"] = "2026-10-15T15:01:00Z"
    run = until_review(review("content"), rejected, review("delivery"))
    with pytest.raises(DeliveryBlocked, match="rejected or asked for changes"):
        deliver(run.store, requested=draft_of(run).ref, platform="simulated", at=DELIVER_AT)


def test_a_scope_with_delivery_disabled_cannot_deliver() -> None:
    scope = variant(load_inputs(NORTHSTAR).scope, execution={"delivery": "disabled"})
    with pytest.raises(DeliveryBlocked, match="allows 'disabled' delivery"):
        run_scenario(NORTHSTAR, inputs=inputs(scope=scope))


def test_live_delivery_is_not_built() -> None:
    run = until_review(review("content"), review("delivery"))
    draft = draft_of(run)
    live = CustomerScope.model_validate(
        variant(run.store.scope, synthetic=False, execution={"delivery": "live"})
    )
    with pytest.raises(DeliveryBlocked, match="only simulated delivery exists"):
        authorize_delivery(
            scope=live,
            requested=draft.ref,
            latest=draft,
            target=DeliveryTarget(platform="instantly", recipient_id="per-jordan-avery"),
            approvals=run.store.list_approvals(draft.ref.artifact_id),
            at=DELIVER_AT,
        )


def test_live_platforms_are_refused() -> None:
    run = until_review(review("content"), review("delivery"))
    with pytest.raises(DeliveryBlocked, match="only simulated delivery exists"):
        deliver(run.store, requested=draft_of(run).ref, platform="instantly", at=DELIVER_AT)


def test_an_outcome_needs_a_delivery() -> None:
    run = until_review(review("content"), review("delivery"))
    report = load_inputs(NORTHSTAR).outcome
    assert report is not None
    with pytest.raises(ValueError, match="never delivered"):
        record_outcome(run.store, artifact_id="draft-northstar-001", report=report)


# Another customer's scope


@pytest.fixture
def other_scope() -> CustomerScope:
    return CustomerScope(
        customer_id="demo-other",
        synthetic=True,
        data_namespace="auggie_demo_other",
        credential_namespace="demo-other",
        knowledge_namespace="demo-other-strategy",
        execution=ExecutionBoundary(delivery="simulated"),
    )


def test_another_scope_cannot_see_review_or_deliver_the_draft(
    other_scope: CustomerScope,
) -> None:
    backend = InMemoryBackend()
    run = until_review(review("content"), review("delivery"), backend=backend)
    other = backend.open(other_scope)
    ref = draft_of(run).ref
    assert other.list_evidence() == () and other.list_events() == ()
    with pytest.raises(RecordNotFound):
        record_review(
            other,
            shown=ref,
            review=ReviewDecision.model_validate(review("content")),
            approval_id="apr-other-content",
        )
    with pytest.raises(RecordNotFound):
        deliver(other, requested=ref, platform="simulated", at=DELIVER_AT)
    assert deliveries(run.store) == 0


def test_a_record_from_another_scope_is_refused(other_scope: CustomerScope) -> None:
    backend = InMemoryBackend()
    run = until_review(backend=backend)
    other = backend.open(other_scope)
    with pytest.raises(ScopeViolation):
        other.put_evidence(run.store.get_evidence("ev-northstar-careers"))
    with pytest.raises(ScopeViolation):
        other.append_event(run.store.list_events()[0])


def test_another_customers_strategy_or_account_cannot_run_in_this_scope(
    tmp_path: Path, other_scope: CustomerScope
) -> None:
    backend = InMemoryBackend()
    run = until_review(backend=backend)
    other = backend.open(other_scope)
    northstar = load_inputs(NORTHSTAR)
    times = northstar.run
    with pytest.raises(ScopeViolation, match="'demo-msp'"):
        activate_strategy(
            other,
            run.strategy,
            by=northstar.strategy.activated_by,
            at=northstar.strategy.activated_at,
        )
    with pytest.raises(ScopeViolation, match="'demo-msp'"):
        research_run(
            other,
            strategy=run.strategy,
            account=northstar.account,
            acquisition=FixtureAcquisition(northstar.responses),
            qualifier=RulesQualifier(),
            times=times,
        )
    text = (NORTHSTAR / "inputs/strategy/msp.toml").read_text(encoding="utf-8")
    (tmp_path / "other.toml").write_text(text.replace('"demo-msp"', '"demo-other"'))
    own_strategy = load_strategy(tmp_path, "other.toml", commit="0" * 40, scope=other_scope)
    with pytest.raises(ScopeViolation, match="'demo-msp'"):
        research_run(
            other,
            strategy=own_strategy,
            account=northstar.account,
            acquisition=FixtureAcquisition(northstar.responses),
            qualifier=RulesQualifier(),
            times=times,
        )
    assert other.list_events() == () and other.list_evidence() == ()


def test_the_delivery_gate_refuses_another_customers_draft_or_approvals(
    other_scope: CustomerScope,
) -> None:
    run = until_review(review("content"), review("delivery"))
    draft = draft_of(run)
    with pytest.raises(ScopeViolation, match="'demo-msp'"):
        authorize_delivery(
            scope=other_scope,
            requested=draft.ref,
            latest=draft,
            target=DeliveryTarget(platform="simulated", recipient_id="per-jordan-avery"),
            approvals=run.store.list_approvals(draft.ref.artifact_id),
            at=DELIVER_AT,
        )
    foreign = [
        HumanApproval.model_validate(variant(a, customer_id="demo-other"))
        for a in run.store.list_approvals(draft.ref.artifact_id)
    ]
    with pytest.raises(ScopeViolation, match="'demo-other'"):
        authorize_delivery(
            scope=run.store.scope,
            requested=draft.ref,
            latest=draft,
            target=DeliveryTarget(platform="simulated", recipient_id="per-jordan-avery"),
            approvals=foreign,
            at=DELIVER_AT,
        )


def test_a_strategy_cannot_be_loaded_into_another_scope(other_scope: CustomerScope) -> None:
    with pytest.raises(ScopeViolation, match="belongs to 'demo-msp'"):
        load_strategy(NORTHSTAR / "inputs", "strategy/msp.toml", commit="0" * 40, scope=other_scope)
