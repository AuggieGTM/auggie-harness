"""The operator's flow for one prospect: research, review, deliver, record what happened.

Each step is a separate call, made by a person when they are ready, through the
same pipeline functions as the synthetic scenario. Nothing runs in the
background (ADR 0007).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from auggie.accounts import Account
from auggie.acquisition import Acquisition
from auggie.acquisition.fixture import RecordedResponse
from auggie.approval import ReviewDecision
from auggie.contracts import (
    Actor,
    Artifact,
    HumanApproval,
    QualificationDecision,
    RecordRef,
    ensure_in_scope,
)
from auggie.contracts.approval import ApprovalDecision
from auggie.contracts.events import OutcomeKind
from auggie.judgment.rules import RulesQualifier
from auggie.pipeline import (
    OutcomeReport,
    RunResult,
    RunTimes,
    activate_strategy,
    complete_revision,
    deliver,
    record_outcome,
    record_review,
    record_revision,
    research_run,
    was_delivered,
)
from auggie.store import Appended, Store, StoreError
from auggie.strategy import LoadedStrategy

Clock = Callable[[], datetime]
Ask = Callable[[str], str]
Print = Callable[[str], None]
Edit = Callable[[str], str]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_run_id(at: datetime) -> str:
    return f"run-{at:%Y%m%dT%H%M%SZ}-{secrets.token_hex(4)}"


def _fresh_run_id(store: Store) -> str:
    """A run ID no event in this store carries yet, stamped with the real time.

    The real time, not the run's clock: a replay is judged as of its
    recordings, but each replay is still a new run.
    """
    for _ in range(5):
        run_id = new_run_id(utc_now())
        if not store.list_events(run_id=run_id):
            return run_id
    raise StoreError("could not find an unused run ID")


def research_prospect(
    store: Store,
    *,
    strategy: LoadedStrategy,
    prospect: Account,
    acquisition: Acquisition,
    operator: str,
    now: Clock = utc_now,
) -> RunResult:
    """Research one prospect from its sources up to a draft, then stop for review.

    The prospect and the strategy must belong to the store's customer; nothing
    is written otherwise. Each stage is stamped by ``now`` as it happens.
    """
    ensure_in_scope(store.scope, prospect)
    ensure_in_scope(store.scope, strategy.strategy)
    activate_strategy(store, strategy, by=Actor(kind="human", id=operator), at=now())
    started = now()
    times = RunTimes(
        run_id=_fresh_run_id(store),
        validated_at=started,
        qualified_at=started,
        composed_at=started,
    )
    return research_run(
        store,
        strategy=strategy,
        account=prospect,
        acquisition=acquisition,
        qualifier=RulesQualifier(),
        times=times,
        per_run_ids=True,
        clock=now,
    )


def replay_clock(responses: Sequence[RecordedResponse]) -> Clock:
    """The clock for a rehearsal: the moment the recorded responses were last observed.

    Judging recorded evidence against today's date would let it go stale, and
    a rehearsal must give the same result whenever it is run.
    """
    as_of = max(response.observed_at for response in responses)
    return lambda: as_of


def describe_draft(store: Store, draft: Artifact, out: Print) -> None:
    """Show a reviewer what they are approving: the exact text and what it rests on."""
    decision: QualificationDecision = store.get_qualification(draft.qualification_id)
    out(f"draft     {draft.ref.artifact_id}, version sha256 {draft.ref.content_hash[7:19]}")
    out(f"          for {draft.recipient_id} at {draft.account_id}, composed {draft.composed_at}")
    out("")
    for line in draft.content.splitlines():
        out(f"          | {line}")
    out("")
    ref = decision.strategy
    out(f"judged    {decision.outcome} under {ref.path}@{ref.commit[:7]}")
    for finding in decision.findings:
        out(f"          {finding.assessment:<13} {finding.criterion}: {finding.rationale}")
    out("rests on")
    for evidence_id in draft.evidence_ids:
        evidence = store.get_evidence(evidence_id)
        out(f"          {evidence_id}: {evidence.source_url} (observed {evidence.observed_at})")


def _next_id(store: Store, artifact_id: str, scope: str) -> str:
    count = sum(a.scope == scope for a in store.list_approvals(artifact_id))
    return f"apr-{artifact_id}-{scope}-{count + 1}"


def review_draft(
    store: Store,
    *,
    draft_id: str,
    reviewer: str,
    ask: Ask,
    out: Print,
    delivery_days: int = 7,
    now: Clock = utc_now,
) -> list[HumanApproval]:
    """Ask a person about the latest version of a draft and record their decisions.

    Content first; delivery is asked only if the content was approved.
    """
    _finish_last_revision(store, draft_id, out)
    draft = store.latest_artifact(draft_id)
    describe_draft(store, draft, out)
    person = Actor(kind="human", id=reviewer)
    answer = ask("Approve this version's content? [y]es / [n]o / [c]hanges: ").strip().lower()
    choices: dict[str, ApprovalDecision] = {
        "y": "approved",
        "yes": "approved",
        "c": "changes_requested",
        "changes": "changes_requested",
    }
    decision = choices.get(answer, "rejected")
    note = ask("Note to keep with your decision (Enter for none): ").strip() or None
    content = ReviewDecision(
        scope="content",
        decision=decision,
        reviewer=person,
        decided_at=now(),
        note=note,
    )
    approvals = [
        record_review(
            store,
            shown=draft.ref,
            review=content,
            approval_id=_next_id(store, draft_id, "content"),
        )
    ]
    if decision != "approved":
        return approvals
    question = (
        f"Approve delivery to {draft.recipient_id} (simulated), valid {delivery_days} days? [y/N]: "
    )
    if ask(question).strip().lower() in ("y", "yes"):
        decided = now()
        delivery = ReviewDecision(
            scope="delivery",
            platform="simulated",
            decision="approved",
            reviewer=person,
            decided_at=decided,
            expires_at=decided + timedelta(days=delivery_days),
        )
        approvals.append(
            record_review(
                store,
                shown=draft.ref,
                review=delivery,
                approval_id=_next_id(store, draft_id, "delivery"),
            )
        )
    return approvals


def _finish_last_revision(store: Store, draft_id: str, out: Print) -> None:
    if complete_revision(store, draft_id):
        out(f"recorded  the last revision of {draft_id}, which an earlier run left unfinished")


def revise_draft(
    store: Store,
    *,
    draft_id: str,
    author: str,
    edit: Edit,
    out: Print,
    now: Clock = utc_now,
) -> Artifact | None:
    """Let a person edit the latest version of a draft, and store the edit as its next version.

    Shows the reviewers' notes on that version first. Records nothing and
    returns None when the text comes back unchanged. A delivered draft is
    not revised; research again for a new one.
    """
    _finish_last_revision(store, draft_id, out)
    draft = store.latest_artifact(draft_id)
    if draft.recipient_id is not None and was_delivered(store, draft_id, draft.recipient_id):
        raise ValueError(f"{draft_id} was already delivered; research again for a new draft")
    for approval in store.list_approvals(draft_id):
        if approval.artifact == draft.ref and approval.note:
            out(f"review    {approval.decision} by {approval.reviewer.id}: {approval.note}")
    content = edit(draft.content)
    if content == draft.content:
        out(f"unchanged {draft_id}: nothing recorded")
        return None
    if not content.strip():
        raise ValueError("the edited draft is empty; nothing recorded")
    # Checked again as it is written: the draft may have been delivered while it was open.
    return record_revision(
        store, previous=draft.ref, content=content, by=Actor(kind="human", id=author), at=now()
    )


def deliver_draft(store: Store, *, draft_id: str, now: Clock = utc_now) -> Appended:
    """Hand the latest version off (simulated) if it is authorized now; raise otherwise."""
    draft = store.latest_artifact(draft_id)
    return deliver(store, requested=draft.ref, platform="simulated", at=now())


def report_outcome(
    store: Store,
    *,
    draft_id: str,
    outcome: OutcomeKind,
    reporter: str,
    now: Clock = utc_now,
) -> Appended:
    """Record what happened after a delivery, as the next report for that draft."""
    artifact = RecordRef(kind="artifact", id=draft_id)
    earlier = sum(artifact in e.refs for e in store.list_events(type="outcome.recorded"))
    report = OutcomeReport(
        outcome=outcome,
        reported_by=Actor(kind="human", id=reporter),
        occurred_at=now(),
        sequence=earlier + 1,
    )
    return record_outcome(store, artifact_id=draft_id, report=report)
