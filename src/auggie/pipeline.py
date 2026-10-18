"""The first vertical slice as plain sequential calls through the Store.

``research_run`` goes from sources to a draft and stops there: approval,
delivery and outcomes are separate calls a person triggers. There are no
retries, leases or background work; this is not a scheduler (ADR 0007).

Every write goes through the Store, together with the event that records it.
IDs are readable and derived from the account, so re-running the same inputs
writes nothing new.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from auggie.accounts import Account
from auggie.acquisition import Acquisition
from auggie.acquisition.validation import VALIDATOR, Verdict, check_evidence
from auggie.approval import ReviewDecision, approval_for
from auggie.compose.template import COMPOSER, compose_draft, select_play
from auggie.contracts import (
    Actor,
    Artifact,
    ArtifactRef,
    Contract,
    DeliveryTarget,
    HumanApproval,
    OperationalEvent,
    QualificationDecision,
    RecordRef,
    SubjectRef,
    ensure_in_scope,
    sha256_digest,
)
from auggie.contracts.approval import DeliveryPlatform
from auggie.contracts.common import RecordId, UtcDatetime
from auggie.contracts.events import AttributeValue, EventType, OutcomeKind
from auggie.delivery import SIMULATED_DELIVERY, authorize_delivery
from auggie.judgment import Qualifier
from auggie.store import Appended, Store
from auggie.strategy import LoadedStrategy


class RunTimes(Contract):
    """When each automated step of a run happens. Real runs take these from the clock."""

    run_id: RecordId
    validated_at: UtcDatetime
    qualified_at: UtcDatetime
    composed_at: UtcDatetime


class OutcomeReport(Contract):
    """An outcome a person reports for a delivered draft. ``sequence`` numbers reports."""

    outcome: OutcomeKind
    reported_by: Actor
    occurred_at: UtcDatetime
    sequence: int = 1


@dataclass(frozen=True)
class RunResult:
    verdicts: tuple[Verdict, ...]
    decision: QualificationDecision
    draft: Artifact | None
    stopped: str | None = None
    """Why no draft was composed, when there is none."""


def _append(
    store: Store,
    type: EventType,
    *,
    at: datetime,
    actor: Actor,
    refs: list[RecordRef],
    key: str,
    run_id: str | None = None,
    attributes: dict[str, AttributeValue] | None = None,
) -> Appended:
    return store.append_event(
        OperationalEvent(
            event_id=f"evt-{len(store.list_events()) + 1:03d}",
            customer_id=store.scope.customer_id,
            type=type,
            occurred_at=at,
            actor=actor,
            refs=tuple(refs),
            idempotency_key=key,
            run_id=run_id,
            attributes=attributes or {},
        )
    )


def activate_strategy(
    store: Store, strategy: LoadedStrategy, *, by: Actor, at: datetime
) -> Appended:
    """Record that a person made this strategy revision the active one."""
    ensure_in_scope(store.scope, strategy.strategy)
    strategy_id = strategy.strategy.strategy_id
    return _append(
        store,
        "strategy.activated",
        at=at,
        actor=by,
        refs=[RecordRef(kind="strategy", id=strategy_id)],
        key=f"strategy.activated:{strategy_id}",
    )


def research_run(
    store: Store,
    *,
    strategy: LoadedStrategy,
    account: Account,
    acquisition: Acquisition,
    qualifier: Qualifier,
    times: RunTimes,
    per_run_ids: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> RunResult:
    """Observe, validate, judge and compose for one account, then stop for review.

    Each stage is stamped from ``clock`` when one is given, at the moment it
    happens; otherwise from the planned ``times``, as synthetic scenarios do.

    Record IDs are readable and derived from the account (``ev-northstar-careers``),
    so re-running the same inputs writes nothing new. With ``per_run_ids`` they
    also carry the run ID, so researching the same prospect again later makes
    new records rather than conflicting with the old ones.

    The strategy and the account must belong to the store's customer; nothing
    is acquired or written otherwise.
    """
    ensure_in_scope(store.scope, strategy.strategy)
    ensure_in_scope(store.scope, account)
    customer_id = store.scope.customer_id
    subject = SubjectRef(kind="account", id=account.account_id)
    run_id = times.run_id
    suffix = f"-{run_id}" if per_run_ids else ""

    def at(planned: datetime) -> datetime:
        """When a stage happens: from the clock if given, else as planned in ``times``."""
        return clock() if clock is not None else planned

    sources: dict[str, Verdict] = {}
    for kind in strategy.strategy.sources:
        url = account.sources.get(kind)
        if url is None:
            continue
        evidence, details = acquisition.acquire(
            evidence_id=f"ev-{account.key}-{kind}{suffix}",
            customer_id=customer_id,
            subject=subject,
            url=url,
        )
        store.put_evidence(evidence)
        _append(
            store,
            "evidence.recorded",
            at=evidence.observed_at,
            actor=acquisition.actor,
            refs=[RecordRef(kind="evidence", id=evidence.evidence_id)],
            key=f"evidence.recorded:{evidence.evidence_id}",
            run_id=run_id,
            attributes={**details, **({} if evidence.usable else {"status": evidence.status})},
        )
        sources[kind] = check_evidence(evidence, scope=store.scope, subject=subject)

    validated_at = at(times.validated_at)
    for verdict in sources.values():
        evidence_id = verdict.evidence.evidence_id
        type: EventType = "evidence.validated" if verdict.admitted else "evidence.rejected"
        _append(
            store,
            type,
            at=validated_at,
            actor=VALIDATOR,
            refs=[RecordRef(kind="evidence", id=evidence_id)],
            key=f"{type}:{evidence_id}",
            run_id=run_id,
            attributes={} if verdict.admitted else {"reason": verdict.rejected_for},
        )

    qualified_at = at(times.qualified_at)
    decision = qualifier.qualify(
        decision_id=f"qual-{account.key}{suffix or '-001'}",
        customer_id=customer_id,
        account_id=account.account_id,
        strategy=strategy,
        sources=sources,
        at=qualified_at,
    )
    store.put_qualification(decision)
    _append(
        store,
        "qualification.decided",
        at=qualified_at,
        actor=qualifier.actor,
        refs=[
            RecordRef(kind="qualification", id=decision.decision_id),
            RecordRef(kind="account", id=account.account_id),
        ],
        key=f"qualification.decided:{decision.decision_id}",
        run_id=run_id,
        attributes={"outcome": decision.outcome},
    )

    verdicts = tuple(sources.values())
    if decision.outcome != "qualified":
        return RunResult(verdicts, decision, None, f"the account is {decision.outcome}")
    play = select_play(strategy, decision)
    if play is None:
        return RunResult(verdicts, decision, None, "no play's criteria are all met")
    composed_at = at(times.composed_at)
    draft = compose_draft(
        artifact_id=f"draft-{account.key}{suffix or '-001'}",
        play=play,
        strategy=strategy,
        decision=decision,
        account=account,
        sources=sources,
        at=composed_at,
    )
    store.put_artifact(draft)
    _append(
        store,
        "draft.composed",
        at=composed_at,
        actor=COMPOSER,
        refs=[
            RecordRef(kind="artifact", id=draft.ref.artifact_id),
            RecordRef(kind="qualification", id=decision.decision_id),
            *(RecordRef(kind="evidence", id=eid) for eid in draft.evidence_ids),
        ],
        key=f"draft.composed:{draft.ref.artifact_id}",
        run_id=run_id,
    )
    return RunResult(verdicts, decision, draft)


def record_revision(
    store: Store, *, previous: Artifact, content: str, by: Actor, at: datetime
) -> Artifact:
    """Store a person's edit of ``previous`` as the artifact's next version (ADR 0014).

    The new version keeps what ``previous`` was composed from. Approvals bind
    to one hash, so none of the earlier versions' approvals apply to it.
    """
    ref = previous.ref
    revised = Artifact(
        customer_id=previous.customer_id,
        ref=ArtifactRef(
            artifact_id=ref.artifact_id, kind=ref.kind, content_hash=sha256_digest(content)
        ),
        content=content,
        account_id=previous.account_id,
        recipient_id=previous.recipient_id,
        qualification_id=previous.qualification_id,
        evidence_ids=previous.evidence_ids,
        strategy=previous.strategy,
        composed_by=by,
        composed_at=at,
        supersedes=ref.content_hash,
    )
    store.put_artifact(revised)
    _append(
        store,
        "draft.composed",
        at=at,
        actor=by,
        refs=[
            RecordRef(kind="artifact", id=ref.artifact_id),
            RecordRef(kind="qualification", id=revised.qualification_id),
            *(RecordRef(kind="evidence", id=eid) for eid in revised.evidence_ids),
        ],
        key=f"draft.composed:{ref.artifact_id}:{revised.ref.content_hash}",
        attributes={"supersedes": ref.content_hash},
    )
    return revised


def was_delivered(store: Store, artifact_id: str, recipient_id: str) -> bool:
    """Whether a hand-off of ``artifact_id`` to ``recipient_id`` is recorded."""
    return any(
        RecordRef(kind="artifact", id=artifact_id) in event.refs
        and RecordRef(kind="person", id=recipient_id) in event.refs
        for event in store.list_events(type="delivery.simulated")
    )


def record_review(
    store: Store, *, shown: ArtifactRef, review: ReviewDecision, approval_id: str
) -> HumanApproval:
    """Record a person's decision about exactly the version they were shown."""
    approval = approval_for(store.get_artifact(shown), review, approval_id=approval_id)
    store.put_approval(approval)
    _append(
        store,
        "approval.recorded",
        at=approval.decided_at,
        actor=approval.reviewer,
        refs=[
            RecordRef(kind="approval", id=approval.approval_id),
            RecordRef(kind="artifact", id=shown.artifact_id),
        ],
        key=f"approval.recorded:{approval.approval_id}",
    )
    return approval


def deliver(
    store: Store, *, requested: ArtifactRef, platform: DeliveryPlatform, at: datetime
) -> Appended:
    """Hand ``requested`` off if, and only if, it is authorized now.

    Raises ``DeliveryBlocked`` otherwise. Delivering the same draft to the
    same recipient again returns the first delivery with ``created=False``.
    """
    latest = store.latest_artifact(requested.artifact_id)
    if latest.recipient_id is None:
        raise ValueError(f"{requested.artifact_id} has no recipient")
    target = DeliveryTarget(platform=platform, recipient_id=latest.recipient_id)
    approval = authorize_delivery(
        scope=store.scope,
        requested=requested,
        latest=latest,
        target=target,
        approvals=store.list_approvals(requested.artifact_id),
        at=at,
    )
    return _append(
        store,
        "delivery.simulated",
        at=at,
        actor=SIMULATED_DELIVERY,
        refs=[
            RecordRef(kind="approval", id=approval.approval_id),
            RecordRef(kind="artifact", id=requested.artifact_id),
            RecordRef(kind="person", id=target.recipient_id),
        ],
        key=f"delivery.simulated:{requested.artifact_id}:{target.recipient_id}",
    )


def record_outcome(store: Store, *, artifact_id: str, report: OutcomeReport) -> Appended:
    """Record what happened after a delivery. There must have been one."""
    recipient_id = store.latest_artifact(artifact_id).recipient_id
    if recipient_id is None:
        raise ValueError(f"{artifact_id} has no recipient")
    if not was_delivered(store, artifact_id, recipient_id):
        raise ValueError(f"{artifact_id} was never delivered to {recipient_id}")
    return _append(
        store,
        "outcome.recorded",
        at=report.occurred_at,
        actor=report.reported_by,
        refs=[
            RecordRef(kind="artifact", id=artifact_id),
            RecordRef(kind="person", id=recipient_id),
        ],
        key=f"outcome.recorded:{artifact_id}:{recipient_id}:{report.sequence}",
        attributes={"outcome": report.outcome},
    )
