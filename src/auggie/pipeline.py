"""The first vertical slice as plain sequential calls through the Store.

``research_run`` goes from sources to a draft and stops there: approval,
delivery and outcomes are separate calls a person triggers. There are no
retries, leases or background work; this is not a scheduler (ADR 0007).

Every write goes through the Store, together with the event that records it.
IDs are readable and derived from the account, so re-running the same inputs
writes nothing new.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import HttpUrl, TypeAdapter, ValidationError

from auggie.accounts import Account
from auggie.acquisition import Acquisition
from auggie.acquisition.careers import ListingReader
from auggie.acquisition.validation import VALIDATOR, Verdict, check_evidence
from auggie.approval import ReviewDecision, approval_for
from auggie.compose.template import COMPOSER, compose_draft, select_play
from auggie.contracts import (
    Actor,
    Artifact,
    ArtifactRef,
    CareersSnapshot,
    CitationError,
    Contract,
    DeliveryTarget,
    HumanApproval,
    OperationalEvent,
    Person,
    QualificationDecision,
    RecordRef,
    SubjectRef,
    ensure_in_scope,
    sha256_digest,
)
from auggie.contracts.approval import DeliveryPlatform
from auggie.contracts.common import RecordId, UtcDatetime
from auggie.contracts.events import AttributeValue, EventType, OutcomeKind
from auggie.contracts.strategy import LISTING_CRITERIA
from auggie.delivery import (
    SIMULATED_DELIVERY,
    DeliveryBlocked,
    Sender,
    SendRefused,
    SendUnknown,
    authorize_delivery,
    split_message,
)
from auggie.external import require_external_calls
from auggie.judgment import ListingReadings, Qualifier
from auggie.judgment.rules import hiring_finding
from auggie.store import Appended, RecordConflict, RecordNotFound, Store
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
    snapshot: CareersSnapshot | None = None
    """This run's read of the account's careers listing, if it read one."""


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
        _event(
            store,
            type,
            at=at,
            actor=actor,
            refs=refs,
            key=key,
            run_id=run_id,
            attributes=attributes,
        )
    )


def _event(
    store: Store,
    type: EventType,
    *,
    at: datetime,
    actor: Actor,
    refs: list[RecordRef],
    key: str,
    run_id: str | None = None,
    attributes: dict[str, AttributeValue] | None = None,
) -> OperationalEvent:
    return OperationalEvent(
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


def record_person(store: Store, *, account: Account, by: Actor, at: datetime) -> Person:
    """Store the account's recipient as a person record, once (ADR 0022).

    A person is never edited, so a recipient whose address changed needs a
    new ``person_id``; the same recipient again changes nothing.
    """
    recipient = account.recipient
    person = Person(
        customer_id=account.customer_id,
        person_id=recipient.person_id,
        account_id=account.account_id,
        first_name=recipient.first_name,
        email=recipient.email,
    )
    try:
        stored = store.get_person(person.person_id)
    except RecordNotFound:
        stored = None
    if stored is not None and stored != person:
        raise ValueError(
            f"{person.person_id} is already recorded with other details; "
            "give the recipient a new person_id"
        )
    store.put_person(person)
    _append(
        store,
        "person.recorded",
        at=at,
        actor=by,
        refs=[
            RecordRef(kind="person", id=person.person_id),
            RecordRef(kind="account", id=person.account_id),
        ],
        key=f"person.recorded:{person.person_id}",
    )
    return person


def _observe(
    store: Store,
    acquisition: Acquisition,
    evidence_id: str,
    subject: SubjectRef,
    url: HttpUrl | str,
    run_id: str | None,
) -> Verdict:
    """Acquire one page, store it as evidence with its event, and check it."""
    evidence, details = acquisition.acquire(
        evidence_id=evidence_id,
        customer_id=store.scope.customer_id,
        subject=subject,
        url=HttpUrl(str(url)),
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
    return check_evidence(evidence, scope=store.scope, subject=subject)


def _record_verdict(store: Store, verdict: Verdict, at: datetime, run_id: str | None) -> None:
    evidence_id = verdict.evidence.evidence_id
    type: EventType = "evidence.validated" if verdict.admitted else "evidence.rejected"
    _append(
        store,
        type,
        at=at,
        actor=VALIDATOR,
        refs=[RecordRef(kind="evidence", id=evidence_id)],
        key=f"{type}:{evidence_id}",
        run_id=run_id,
        attributes={} if verdict.admitted else {"reason": verdict.rejected_for},
    )


def record_page(
    store: Store,
    *,
    acquisition: Acquisition,
    account_id: str,
    evidence_id: str,
    url: str,
    run_id: str,
    now: Callable[[], datetime],
) -> Verdict:
    """Read one page a person named for an account, and record it as checked evidence
    (ADR 0025). A failed read is recorded too, with its reason.

    Every ID is checked before anything is read or written, and the check is stamped by
    ``now`` after the read, when it happens.
    """
    for value, what in (
        (account_id, "account ID"),
        (evidence_id, "evidence ID"),
        (run_id, "run ID"),
    ):
        try:
            _RECORD_ID.validate_python(value)
        except ValidationError:
            raise ValueError(f"not a valid {what}: {value!r}") from None
    subject = SubjectRef(kind="account", id=account_id)
    verdict = _observe(store, acquisition, evidence_id, subject, url, run_id)
    _record_verdict(store, verdict, now(), run_id)
    return verdict


_RECORD_ID: TypeAdapter[str] = TypeAdapter(RecordId)


def record_snapshot(
    store: Store,
    *,
    reader: ListingReader,
    account: Account,
    snapshot_id: str,
    run_id: str | None,
) -> CareersSnapshot:
    """Read the account's careers listing once, and record the snapshot with its event,
    whatever the outcome (ADR 0024). A failed read is kept, with its reason.

    Every ID is checked before the listing is read, and the event is built before the
    snapshot is stored, so a bad input leaves no record without its event.
    """
    ensure_in_scope(store.scope, account)
    if account.careers_listing is None:
        raise ValueError(f"{account.account_id} names no careers listing")
    for value, what in ((snapshot_id, "snapshot ID"), (run_id, "run ID")):
        try:
            if value is not None:
                _RECORD_ID.validate_python(value)
        except ValidationError:
            raise ValueError(f"not a valid {what}: {value!r}") from None
    snapshot = reader.read(
        listing=account.careers_listing,
        customer_id=store.scope.customer_id,
        account_id=account.account_id,
        snapshot_id=snapshot_id,
    )
    if (
        snapshot.snapshot_id != snapshot_id
        or snapshot.account_id != account.account_id
        or snapshot.listing != account.careers_listing
    ):
        # The listing is named, never guessed: a reply about another one is not recorded.
        raise ValueError("the reader returned a snapshot of something else")
    event = _event(
        store,
        "careers.recorded",
        at=snapshot.read_at,
        actor=reader.actor,
        refs=[
            RecordRef(kind="careers", id=snapshot.snapshot_id),
            RecordRef(kind="account", id=account.account_id),
        ],
        key=f"careers.recorded:{snapshot.snapshot_id}",
        run_id=run_id,
        attributes={"status": snapshot.status, "roles": len(snapshot.roles)},
    )
    store.put_careers_snapshot(snapshot)
    store.append_event(event)
    return snapshot


def reads_listing(strategy: LoadedStrategy, account: Account) -> bool:
    """Whether research reads the account's careers listing: the strategy counts hiring and
    the prospect names a listing."""
    counts_hiring = any(isinstance(c, LISTING_CRITERIA) for c in strategy.strategy.criteria)
    return counts_hiring and account.careers_listing is not None


def _read_listing(
    store: Store,
    strategy: LoadedStrategy,
    account: Account,
    reader: ListingReader | None,
    snapshot_id: str,
    run_id: str,
) -> ListingReadings | None:
    """This run's read of the account's listing, when the strategy counts hiring and the
    prospect names a listing; with every earlier snapshot of the account."""
    if reader is None or not reads_listing(strategy, account):
        return None
    current = record_snapshot(
        store, reader=reader, account=account, snapshot_id=snapshot_id, run_id=run_id
    )
    earlier = tuple(
        s
        for s in store.list_careers_snapshots(account.account_id)
        if s.snapshot_id != current.snapshot_id
    )
    return ListingReadings(current, earlier)


def _check_hiring_findings(
    decision: QualificationDecision, strategy: LoadedStrategy, listing: ListingReadings | None
) -> None:
    """Whatever the qualifier, each hiring finding is the rules' tally of this run's read of
    the listing and its baseline: a tally is never written by a model (ADR 0024). So none
    settles from an older read, omits its baseline, or claims more than a partial read shows.
    No other finding cites a snapshot."""
    rules = strategy.strategy
    problems: list[str] = []
    for finding in decision.findings:
        try:
            criterion = rules.criterion(finding.criterion)
        except KeyError:
            problems.append(f"{finding.criterion}: not a criterion of {rules.strategy_id}")
            continue
        if not isinstance(criterion, LISTING_CRITERIA):
            if finding.snapshot_ids:
                problems.append(f"{finding.criterion}: only a hiring criterion cites snapshots")
            continue
        tally = hiring_finding(criterion, rules, listing)
        if finding != tally:
            problems.append(
                f"{finding.criterion}: the rules' tally of this run's read is {tally.assessment}"
                f" citing {', '.join(tally.snapshot_ids) or 'no snapshot'}, not"
                f" {finding.assessment} citing {', '.join(finding.snapshot_ids) or 'no snapshot'}"
            )
    if problems:
        raise CitationError("; ".join(problems))


def _check_outcome(decision: QualificationDecision, strategy: LoadedStrategy) -> None:
    """Whatever the qualifier, the decision assesses every criterion of its strategy, and its
    outcome follows the strategy's rules: qualified only when every required criterion is met,
    and disqualified only by a criterion the strategy marks as disqualifying."""
    rules = strategy.strategy
    if decision.strategy != strategy.ref:
        raise CitationError("the decision names another strategy revision than the run's")
    assessed = {f.criterion: f.assessment for f in decision.findings}
    problems = [f"{c.id}: not assessed" for c in rules.criteria if c.id not in assessed]
    if decision.outcome == "qualified":
        problems += [
            f"{name}: required, but {assessed[name]}"
            for name in rules.required
            if name in assessed and assessed[name] != "meets"
        ]
    for name in decision.disqualifiers:
        if not getattr(rules.criterion(name), "disqualifying", False):
            problems.append(f"{name}: the strategy does not let it disqualify")
    if problems:
        raise CitationError(
            f"{decision.outcome} does not follow {rules.strategy_id}: " + "; ".join(problems)
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
    listings: ListingReader | None = None,
) -> RunResult:
    """Observe, validate, judge and compose for one account, then stop for review.

    When the strategy counts hiring and the account names a careers listing,
    ``listings`` reads it once; without a reader, those criteria are unknown.

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
        sources[kind] = _observe(
            store, acquisition, f"ev-{account.key}-{kind}{suffix}", subject, url, run_id
        )

    listing = _read_listing(
        store, strategy, account, listings, f"snap-{account.key}{suffix or '-001'}", run_id
    )

    validated_at = at(times.validated_at)
    for verdict in sources.values():
        _record_verdict(store, verdict, validated_at, run_id)

    qualified_at = at(times.qualified_at)
    decision = qualifier.qualify(
        decision_id=f"qual-{account.key}{suffix or '-001'}",
        customer_id=customer_id,
        account_id=account.account_id,
        strategy=strategy,
        sources=sources,
        at=qualified_at,
        listing=listing,
    )
    _check_hiring_findings(decision, strategy, listing)
    _check_outcome(decision, strategy)
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
    read = listing.current if listing is not None else None
    if decision.outcome != "qualified":
        return RunResult(verdicts, decision, None, f"the account is {decision.outcome}", read)
    play = select_play(strategy, decision)
    if play is None:
        return RunResult(verdicts, decision, None, "no play's criteria are all met", read)
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
            *(RecordRef(kind="careers", id=sid) for sid in draft.snapshot_ids),
        ],
        key=f"draft.composed:{draft.ref.artifact_id}",
        run_id=run_id,
    )
    return RunResult(verdicts, decision, draft, snapshot=read)


def record_revision(
    store: Store, *, previous: ArtifactRef, content: str, by: Actor, at: datetime
) -> Artifact:
    """Store a person's edit of the ``previous`` version as the artifact's next one (ADR 0014).

    The new version keeps what the stored ``previous`` was composed from.
    Approvals bind to one hash, so none of the earlier versions' approvals
    apply to it. Nothing is written unless ``previous`` is still the latest
    version, the artifact was never delivered, and the text is new.
    """
    edited = store.get_artifact(previous)
    artifact_id = previous.artifact_id
    if store.latest_artifact(artifact_id).ref != previous:
        raise ValueError(f"{artifact_id} has a newer version than the one edited")
    recipient = edited.recipient_id
    if recipient is not None:
        ensure_undelivered(store, artifact_id, recipient)
    ref = ArtifactRef(
        artifact_id=artifact_id, kind=previous.kind, content_hash=sha256_digest(content)
    )
    try:
        store.get_artifact(ref)
    except RecordNotFound:
        pass
    else:
        raise ValueError(f"that text is an earlier version of {artifact_id}; change it to revise")
    revised = Artifact(
        customer_id=edited.customer_id,
        ref=ref,
        content=content,
        account_id=edited.account_id,
        recipient_id=recipient,
        qualification_id=edited.qualification_id,
        evidence_ids=edited.evidence_ids,
        snapshot_ids=edited.snapshot_ids,
        strategy=edited.strategy,
        composed_by=by,
        composed_at=at,
        supersedes=previous.content_hash,
    )
    _revision_event(store, revised)  # an event that cannot be recorded stops this before any write
    store.put_artifact(revised)
    _append_revision_event(store, revised)
    return revised


def complete_revision(store: Store, artifact_id: str) -> bool:
    """Record the latest version's revision event if a run stopped before recording it.

    The event is rebuilt from the stored version, so this changes nothing
    when the event is already there (ADR 0015). Returns whether it was added.
    """
    latest = store.latest_artifact(artifact_id)
    if (
        latest.supersedes is None
        or latest.composed_by.kind != "human"
        or latest.ref.kind != "draft"
    ):
        return False  # only a person's edit of a draft is recorded this way
    return _append_revision_event(store, latest)


def _append_revision_event(store: Store, revised: Artifact) -> bool:
    """Append the revision's event. An event already under its key must be this same event."""
    event = _revision_event(store, revised)
    appended = store.append_event(event)
    facts = {"event_id"}
    if not appended.created and appended.event.model_dump(
        mode="json", exclude=facts
    ) != event.model_dump(mode="json", exclude=facts):
        raise RecordConflict(f"{event.idempotency_key!r} already records a different revision")
    return appended.created


def _revision_event(store: Store, revised: Artifact) -> OperationalEvent:
    artifact_id = revised.ref.artifact_id
    return _event(
        store,
        "draft.composed",
        at=revised.composed_at,
        actor=revised.composed_by,
        refs=[
            RecordRef(kind="artifact", id=artifact_id),
            RecordRef(kind="qualification", id=revised.qualification_id),
            *(RecordRef(kind="evidence", id=eid) for eid in revised.evidence_ids),
        ],
        # "@" never appears in a record ID, so this cannot match another draft's first key.
        key=f"draft.composed:{artifact_id}@{revised.ref.content_hash.removeprefix('sha256:')[:32]}",
        attributes={"supersedes": revised.supersedes},
    )


DELIVERY_EVENTS = ("delivery.simulated", "delivery.attempted", "delivery.sent", "delivery.failed")


@dataclass(frozen=True)
class DeliveryState:
    """What the records say about delivering one draft to one person (ADR 0022)."""

    attempts: tuple[OperationalEvent, ...]
    sent: OperationalEvent | None
    """The simulated hand-off or the send's receipt, if there is one."""
    unknown: bool
    """The last attempt has neither a receipt nor a failure: it may have gone out."""
    closed: bool
    """A person recorded an attempt as not sent. The harness never retries the draft."""


def delivery_state(store: Store, artifact_id: str, recipient_id: str) -> DeliveryState:
    about = {RecordRef(kind="artifact", id=artifact_id), RecordRef(kind="person", id=recipient_id)}
    events = [e for e in store.list_events() if e.type in DELIVERY_EVENTS and about <= set(e.refs)]
    attempts = tuple(e for e in events if e.type == "delivery.attempted")
    sent = next((e for e in events if e.type in ("delivery.simulated", "delivery.sent")), None)
    failed = [e for e in events if e.type == "delivery.failed"]
    unknown = bool(attempts) and sent is None
    if unknown:
        last = attempts[-1].attributes.get("attempt")
        unknown = not any(e.attributes.get("attempt") == last for e in failed)
    closed = any(e.attributes.get("resolved") is True or e.actor.kind == "human" for e in failed)
    return DeliveryState(attempts, sent, unknown, closed)


def was_delivered(store: Store, artifact_id: str, recipient_id: str) -> bool:
    """Whether a hand-off or a send of ``artifact_id`` to ``recipient_id`` is recorded."""
    return delivery_state(store, artifact_id, recipient_id).sent is not None


def ensure_undelivered(store: Store, artifact_id: str, recipient_id: str) -> None:
    """Refuse to change a draft that went out, may have gone out, or will never go out."""
    state = delivery_state(store, artifact_id, recipient_id)
    if state.sent is not None:
        raise ValueError(f"{artifact_id} was already delivered; research again for a new draft")
    if state.unknown:
        raise ValueError(f"an attempt to send {artifact_id} is unresolved; resolve it first")
    if state.closed:
        raise ValueError(
            f"an attempt to send {artifact_id} was recorded as not sent, and it is never retried; "
            "research again for a new draft"
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
    store: Store,
    *,
    requested: ArtifactRef,
    platform: DeliveryPlatform,
    at: datetime,
    sender: Sender | None = None,
) -> Appended:
    """Hand ``requested`` off, or send it through ``sender``, if and only if it is authorized now.

    Raises ``DeliveryBlocked`` otherwise. Delivering a draft that already went
    to its recipient returns that delivery with ``created=False``. A real send
    is recorded as an attempt before the request, then as sent or failed
    (ADR 0022). An exception from ``sender`` other than ``SendRefused``
    propagates and leaves the attempt unknown, which blocks every later one,
    simulated or live.
    """
    artifact_id = requested.artifact_id
    latest = store.latest_artifact(artifact_id)
    if latest.recipient_id is None:
        raise ValueError(f"{artifact_id} has no recipient")
    recipient = latest.recipient_id
    target = DeliveryTarget(platform=platform, recipient_id=recipient)
    approval = authorize_delivery(
        scope=store.scope,
        requested=requested,
        latest=latest,
        target=target,
        approvals=store.list_approvals(artifact_id),
        at=at,
    )
    person = store.get_person(recipient)
    if person.account_id != latest.account_id:
        raise DeliveryBlocked("the draft's recipient belongs to another account")
    state = delivery_state(store, artifact_id, recipient)
    if state.sent is not None:
        return Appended(state.sent, created=False)
    if state.unknown:
        raise DeliveryBlocked(
            "an earlier attempt to send this draft is unknown: check your Sent folder, then "
            "record what you found with --resolve"
        )
    if state.closed:
        raise DeliveryBlocked(
            "an earlier attempt was recorded as not sent, and the harness never retries it; "
            "research again for a new draft"
        )
    refs = [
        RecordRef(kind="approval", id=approval.approval_id),
        RecordRef(kind="artifact", id=artifact_id),
        RecordRef(kind="person", id=recipient),
    ]
    if platform == "simulated":
        return _append(
            store,
            "delivery.simulated",
            at=at,
            actor=SIMULATED_DELIVERY,
            refs=refs,
            key=f"delivery.simulated:{artifact_id}:{recipient}",
        )
    if sender is None or sender.platform != platform:
        raise DeliveryBlocked(f"no {platform} sender is available")
    if sender.scope != store.scope:
        raise DeliveryBlocked("the sender belongs to another scope")
    require_external_calls(store.scope)
    if person.email is None:
        raise DeliveryBlocked(f"no address is recorded for {recipient}")
    subject, body = split_message(latest.content)
    pair = _pair(artifact_id, recipient)
    attempt = len(state.attempts) + 1
    facts: dict[str, AttributeValue] = {"platform": platform, "attempt": attempt}
    _append(
        store,
        "delivery.attempted",
        at=at,
        actor=sender.actor,
        refs=refs,
        key=f"delivery.attempted:{pair}:{attempt}",
        attributes=facts,
    )
    try:
        receipt = sender.send(to=person.email, subject=subject, body=body)
    except SendRefused as refused:
        reason = _plain(refused.reason, "the platform refused the message")
        _append(
            store,
            "delivery.failed",
            at=at,
            actor=sender.actor,
            refs=refs,
            key=f"delivery.failed:{pair}:{attempt}",
            attributes={**facts, "reason": reason},
        )
        raise DeliveryBlocked(f"nothing was sent: {reason}") from None
    if not RECEIPT.fullmatch(receipt):
        raise SendUnknown("the platform's receipt was not an ID; the message may have been sent")
    return _append(
        store,
        "delivery.sent",
        at=at,
        actor=sender.actor,
        refs=refs,
        key=f"delivery.sent:{pair}",
        attributes={**facts, "receipt": receipt},
    )


RECEIPT = re.compile(r"[A-Za-z0-9_.:-]{1,200}")
"""A platform receipt is an ID. Anything else leaves the send unknown and is never recorded."""


def _pair(artifact_id: str, recipient_id: str) -> str:
    """A bounded key part for one draft and one person. "/" never appears in a record ID."""
    return sha256_digest(f"{artifact_id}/{recipient_id}").removeprefix("sha256:")[:32]


def _plain(reason: str, otherwise: str) -> str:
    """``reason`` if it is one short line of plain text with no address, else ``otherwise``."""
    return reason if re.fullmatch(r"[ -~]{1,300}", reason) and "@" not in reason else otherwise


def resolve_send(
    store: Store, *, artifact_id: str, found: Literal["sent", "not-sent"], by: Actor, at: datetime
) -> Appended:
    """Record what a person found for an unknown send. Either way, it is never retried."""
    if by.kind != "human":
        raise ValueError("only a person can record what happened to a send")
    if found not in ("sent", "not-sent"):
        raise ValueError(f"a send is resolved as sent or not-sent, not {found!r}")
    recipient = store.latest_artifact(artifact_id).recipient_id
    if recipient is None:
        raise ValueError(f"{artifact_id} has no recipient")
    state = delivery_state(store, artifact_id, recipient)
    if not state.unknown:
        raise ValueError(f"{artifact_id} has no unknown send to resolve")
    last = state.attempts[-1]
    pair = _pair(artifact_id, recipient)
    facts = {**last.attributes, "resolved": True}
    if found == "sent":
        return _append(
            store,
            "delivery.sent",
            at=at,
            actor=by,
            refs=list(last.refs),
            key=f"delivery.sent:{pair}",
            attributes=facts,
        )
    return _append(
        store,
        "delivery.failed",
        at=at,
        actor=by,
        refs=list(last.refs),
        key=f"delivery.failed:{pair}:{last.attributes.get('attempt')}",
        attributes={**facts, "reason": "the seller found it was not sent"},
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
