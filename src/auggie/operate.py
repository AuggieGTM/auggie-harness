"""The operator's flow for one prospect: research, review, deliver, record what happened.

Each step is a separate call, made by a person when they are ready, through the
same pipeline functions as the synthetic scenario. Nothing runs in the
background (ADR 0007).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from auggie.accounts import Account
from auggie.acquisition import Acquisition
from auggie.acquisition.careers import ListingReader
from auggie.acquisition.fixture import RecordedListing, RecordedResponse
from auggie.approval import ReviewDecision
from auggie.compose.openrouter import TextModel
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
from auggie.delivery import Sender
from auggie.judgment.rules import RulesQualifier
from auggie.pipeline import (
    OutcomeReport,
    RunResult,
    RunTimes,
    activate_strategy,
    complete_research,
    complete_revision,
    deliver,
    delivery_state,
    ensure_undelivered,
    record_outcome,
    record_person,
    record_review,
    record_revision,
    record_snapshot,
    research_run,
    resolve_send,
)
from auggie.spend import RunBudget
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
    listings: ListingReader | None = None,
    writer: TextModel | None = None,
) -> RunResult:
    """Research one prospect from its sources up to a draft, then stop for review.

    ``listings`` reads the prospect's careers listing when the strategy counts hiring, and
    ``writer`` rewrites the draft of a play written by a model (ADR 0031).

    The prospect and the strategy must belong to the store's customer; nothing
    is written otherwise. Each stage is stamped by ``now`` as it happens.
    """
    ensure_in_scope(store.scope, prospect)
    ensure_in_scope(store.scope, strategy.strategy)
    by = Actor(kind="human", id=operator)
    activate_strategy(store, strategy, by=by, at=now())
    record_person(store, account=prospect, by=by, at=now())
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
        listings=listings,
        writer=writer,
    )


def model_calls(strategy: LoadedStrategy) -> int:
    """The most model calls one account's research makes: two when a play is written by a
    model, a text and one retry (ADR 0031); otherwise none."""
    return 2 if any(play.writer == "model" for play in strategy.strategy.plays) else 0


ListStatus = Literal["researched", "skipped", "waiting", "stopped"]


@dataclass(frozen=True)
class ListLine:
    """What happened to one prospect of a list."""

    account_id: str
    status: ListStatus
    detail: str
    result: RunResult | None = None
    spent_micro_usd: int = 0


def researched_as(store: Store, account_id: str) -> str | None:
    """The newest qualification of the account, if it was ever researched."""
    account = RecordRef(kind="account", id=account_id)
    decided = [
        ref.id
        for event in store.list_events(type="qualification.decided")
        if account in event.refs
        for ref in event.refs
        if ref.kind == "qualification"
    ]
    return decided[-1] if decided else None


def research_list(
    store: Store,
    *,
    strategy: LoadedStrategy,
    prospects: Sequence[Account],
    acquisition: Acquisition,
    operator: str,
    budget: RunBudget | None,
    page_ceiling_micro_usd: int,
    again: bool = False,
    now: Clock = utc_now,
    listings: ListingReader | None = None,
    writer: TextModel | None = None,
) -> list[ListLine]:
    """Research each prospect in order, in this one foreground run (ADR 0027).

    ``budget`` is the list's one cap, shared by every account; None when nothing is paid, as
    in a rehearsal. An account starts only when its worst case fits what is left: one page
    ceiling for each source it names and, when a play is written by a model, the most two
    model calls can cost. The first that does not fit waits, with the rest.
    Accounts already researched are skipped unless ``again``, after finishing any draft event
    their run left unrecorded. An error stops the list.

    The strategy and every prospect must belong to the store's customer, and ``budget`` must
    be the one ``acquisition`` spends from; nothing is read or written otherwise.
    """
    ensure_in_scope(store.scope, strategy.strategy)
    for prospect in prospects:
        ensure_in_scope(store.scope, prospect)
    ids = [p.account_id for p in prospects]
    if len(set(ids)) != len(ids):
        raise ValueError("a list names an account twice")
    if getattr(acquisition, "budget", None) is not budget:
        raise ValueError("the list's budget must be the one its acquisition spends from")
    if writer is not None and writer.budget is not budget:
        raise ValueError("the list's budget must be the one its model spends from")
    calls = model_calls(strategy) if writer is not None else 0
    priced: list[int] = []

    def draft_ceiling() -> int:
        """The most an account's model calls can cost, priced once, when an account first
        needs it; a model that cannot be priced stops the list there."""
        if writer is None or not calls:
            return 0
        if not priced:
            priced.append(calls * writer.most_per_call())
        return priced[0]

    lines: list[ListLine] = []
    for n, prospect in enumerate(prospects):
        account_id = prospect.account_id
        before = budget.spent_micro_usd if budget is not None else 0
        try:
            line = _research_one(
                store,
                strategy=strategy,
                prospect=prospect,
                acquisition=acquisition,
                operator=operator,
                budget=budget,
                page_ceiling_micro_usd=page_ceiling_micro_usd,
                again=again,
                now=now,
                listings=listings,
                writer=writer,
                draft_ceiling=draft_ceiling,
            )
        except Exception as error:
            # Anything that fails, recovery included, stops the list here, and says so.
            spent = (budget.spent_micro_usd if budget is not None else 0) - before
            stopped = f"{type(error).__name__}: {error}"
            line = ListLine(account_id, "stopped", stopped, spent_micro_usd=spent)
        lines.append(line)
        if line.status in ("waiting", "stopped"):
            why = "after the one above" if line.status == "waiting" else "after the stop above"
            lines.extend(ListLine(p.account_id, "waiting", why) for p in prospects[n + 1 :])
            break
    return lines


def _research_one(
    store: Store,
    *,
    strategy: LoadedStrategy,
    prospect: Account,
    acquisition: Acquisition,
    operator: str,
    budget: RunBudget | None,
    page_ceiling_micro_usd: int,
    again: bool,
    now: Clock,
    listings: ListingReader | None,
    writer: TextModel | None = None,
    draft_ceiling: Callable[[], int] = lambda: 0,
) -> ListLine:
    """One prospect of a list: skipped, waiting for room under the cap, or researched."""
    account_id = prospect.account_id
    decided = researched_as(store, account_id)
    if decided is not None and not again:
        detail = f"already researched ({decided})"
        if complete_research(store, decided):
            detail += "; recorded its draft, which that run left unfinished"
        return ListLine(account_id, "skipped", detail)
    pages = sum(kind in prospect.sources for kind in strategy.strategy.sources)
    worst = pages * page_ceiling_micro_usd + draft_ceiling()
    if budget is not None and budget.committed_micro_usd + worst > budget.limit_micro_usd:
        left = budget.limit_micro_usd - budget.committed_micro_usd
        why = f"needs up to {worst} micro-USD, and {left} of {budget.limit_micro_usd} is left"
        return ListLine(account_id, "waiting", why)
    before = budget.spent_micro_usd if budget is not None else 0
    result = research_prospect(
        store,
        strategy=strategy,
        prospect=prospect,
        acquisition=acquisition,
        operator=operator,
        now=now,
        listings=listings,
        writer=writer,
    )
    spent = (budget.spent_micro_usd if budget is not None else 0) - before
    decision = result.decision
    if result.draft is not None:
        detail = f"{decision.outcome}, draft {result.draft.ref.artifact_id}"
    else:
        detail = f"{decision.outcome}, no draft: {result.stopped}"
    return ListLine(account_id, "researched", detail, result, spent)


ScreenStatus = Literal["passed", "screened out", "not screened", "skipped"]


@dataclass(frozen=True)
class ScreenLine:
    """What screening by hiring found for one prospect (ADR 0030)."""

    account_id: str
    status: ScreenStatus
    detail: str


def screen_criteria(strategy: LoadedStrategy) -> list[Any]:
    """The strategy's required hiring criteria, which a screen judges (ADR 0030). Raises
    ``ValueError`` when there are none: there is nothing to screen by."""
    from auggie.contracts.strategy import LISTING_CRITERIA

    rules = strategy.strategy
    required = (rules.criterion(name) for name in rules.required)
    hiring = [c for c in required if isinstance(c, LISTING_CRITERIA)]
    if not hiring:
        raise ValueError(
            "the strategy requires no hiring criterion, so there is nothing to screen by"
        )
    return hiring


def to_screen(store: Store, prospects: Sequence[Account], *, again: bool = False) -> list[Account]:
    """The prospects a screen reads a listing for: those naming one, not yet researched unless
    ``again``."""
    return [
        p
        for p in prospects
        if p.careers_listing is not None and (again or researched_as(store, p.account_id) is None)
    ]


def screen(
    store: Store,
    *,
    strategy: LoadedStrategy,
    prospects: Sequence[Account],
    reader: ListingReader,
    run_id: str,
    again: bool = False,
) -> list[ScreenLine]:
    """Read each prospect's careers listing once, free, and judge the strategy's required
    hiring criteria on it with the rules' own tally (ADR 0030). A prospect passes only when
    every one is met; one shown unmet is screened out; one the read cannot settle, or with no
    listing, is not screened. Prospects already researched are skipped unless ``again``.
    """
    from auggie.judgment import ListingReadings
    from auggie.judgment.rules import hiring_finding

    rules = strategy.strategy
    ensure_in_scope(store.scope, rules)
    for prospect in prospects:
        ensure_in_scope(store.scope, prospect)
    ids = [p.account_id for p in prospects]
    if len(set(ids)) != len(ids):
        raise ValueError("a list names an account twice")
    hiring = screen_criteria(strategy)
    lines = []
    for prospect in prospects:
        account_id = prospect.account_id
        decided = researched_as(store, account_id)
        if decided is not None and not again:
            detail = f"already researched ({decided})"
            if complete_research(store, decided):
                detail += "; recorded its draft, which that run left unfinished"
            lines.append(ScreenLine(account_id, "skipped", detail))
            continue
        if prospect.careers_listing is None:
            lines.append(ScreenLine(account_id, "not screened", "it names no careers listing"))
            continue
        current = record_snapshot(
            store,
            reader=reader,
            account=prospect,
            snapshot_id=f"snap-{prospect.key}-screen-{run_id}",
            run_id=run_id,
        )
        earlier = tuple(
            s
            for s in store.list_careers_snapshots(account_id)
            if s.snapshot_id != current.snapshot_id
        )
        listing = ListingReadings(current, earlier)
        findings = [hiring_finding(c, rules, listing) for c in hiring]
        assessed = {f.assessment for f in findings}
        why = "; ".join(f"{f.criterion}: {f.rationale}" for f in findings)
        if assessed == {"meets"}:
            lines.append(ScreenLine(account_id, "passed", why))
        elif "does_not_meet" in assessed:
            lines.append(ScreenLine(account_id, "screened out", why))
        else:
            lines.append(ScreenLine(account_id, "not screened", why))
    return lines


def review_waiting(
    store: Store,
    *,
    reviewer: str,
    ask: Ask,
    out: Print,
    delivery_days: int = 7,
) -> int:
    """Review the drafts waiting for a person, oldest first, each on its own (ADR 0027),
    asking before each after the first. Returns how many were reviewed."""
    reviewed = 0
    while waiting := waiting_drafts(store):
        if reviewed:
            try:
                go_on = ask(f"{len(waiting)} more waiting. Review the next? [Y/n]: ")
            except EOFError:
                go_on = "n"
            if go_on.strip().lower() in ("n", "no"):
                return reviewed
            out("")
        draft_id = waiting[0]
        out(f"next      {draft_id}, the oldest of {len(waiting)} waiting")
        approvals = review_draft(
            store,
            draft_id=draft_id,
            reviewer=reviewer,
            ask=ask,
            out=out,
            delivery_days=delivery_days,
        )
        for approval in approvals:
            out(f"recorded  {approval.scope} {approval.decision} ({approval.approval_id})")
        reviewed += 1
    out("no drafts are waiting for a person" if not reviewed else "no more drafts are waiting")
    return reviewed


def replay_clock(
    responses: Sequence[RecordedResponse], listings: Sequence[RecordedListing] = ()
) -> Clock:
    """The clock for a rehearsal: the moment the recorded responses were last observed, or
    the recorded listings last read.

    Judging recorded evidence against today's date would let it go stale, and
    a rehearsal must give the same result whenever it is run.
    """
    as_of = max([r.observed_at for r in responses] + [r.read_at for r in listings])
    return lambda: as_of


def describe_draft(store: Store, draft: Artifact, out: Print) -> None:
    """Show a reviewer what they are approving: the exact text and what it rests on."""
    decision: QualificationDecision = store.get_qualification(draft.qualification_id)
    out(f"draft     {draft.ref.artifact_id}, version sha256 {draft.ref.content_hash[7:19]}")
    out(f"          for {draft.recipient_id} at {draft.account_id}, composed {draft.composed_at}")
    if draft.recipient_id is not None:
        person = store.get_person(draft.recipient_id)
        out(f"          to {person.first_name} <{person.email or 'no address recorded'}>")
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
    for snapshot_id in draft.snapshot_ids:
        snapshot = store.get_careers_snapshot(snapshot_id)
        out(
            f"          {snapshot_id}: {snapshot.listing.url} "
            f"(read {snapshot.read_at}, {snapshot.status}, {len(snapshot.roles)} roles)"
        )
    view = draft.point_of_view
    if view is None:
        return
    # The pain is the seller's angle; the proof is the seller's own claim (ADR 0026).
    out(f"pain      {view.pain}, from play {view.play}: an angle, never a fact about the account")
    out(f"          fit: {view.fit}")
    out(f"          limits: {view.limits}")
    for name, value in (
        ("metric", view.metric),
        ("threshold", view.threshold),
        ("consequence", view.consequence),
    ):
        if value is not None:
            out(f"          {name}: {value}")
    proof = view.proof
    if proof is not None:
        read = store.get_evidence(proof.evidence_id)
        out(f'proof     "{proof.claim}"')
        out(
            f"          a vendor claim: {proof.customer} ({proof.kind}), {proof.url}, read "
            f"{read.observed_at} ({proof.evidence_id})"
        )
        if proof.caveat is not None:
            out(f"          caveat: {proof.caveat}")


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
    if (
        draft.recipient_id is not None
        and store.get_person(draft.recipient_id).account_id != draft.account_id
    ):
        raise ValueError("the draft's recipient belongs to another account")
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
    platform = store.scope.execution.platform
    if platform == "simulated":
        where = f"{draft.recipient_id} (simulated)"
    else:
        recipient = store.get_person(draft.recipient_id) if draft.recipient_id else None
        if recipient is None or recipient.email is None:
            out(f"no address is recorded for {draft.recipient_id}, so delivery cannot be approved")
            return approvals
        where = f"{recipient.first_name} <{recipient.email}> from your {platform} mailbox"
    question = f"Approve sending to {where}, valid {delivery_days} days? [y/N]: "
    if ask(question).strip().lower() in ("y", "yes"):
        decided = now()
        delivery = ReviewDecision(
            scope="delivery",
            platform=platform,
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


def waiting_drafts(store: Store) -> list[str]:
    """Drafts waiting for a person, oldest first (ADR 0027): the latest version has no content
    decision, and the draft has not been delivered, may not have gone out, and was not ruled
    out."""
    composed = dict.fromkeys(
        ref.id
        for event in store.list_events(type="draft.composed")
        for ref in event.refs
        if ref.kind == "artifact"
    )
    waiting = []
    for draft_id in composed:
        latest = store.latest_artifact(draft_id)
        approvals = store.list_approvals(draft_id)
        if any(a.scope == "content" and a.artifact == latest.ref for a in approvals):
            continue
        if latest.recipient_id is not None:
            state = delivery_state(store, draft_id, latest.recipient_id)
            if state.sent is not None or state.unknown or state.closed:
                continue
        waiting.append(draft_id)
    return waiting


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
    if draft.recipient_id is not None:
        ensure_undelivered(store, draft_id, draft.recipient_id)
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
    revised = record_revision(
        store, previous=draft.ref, content=content, by=Actor(kind="human", id=author), at=now()
    )
    before = draft.point_of_view.proof if draft.point_of_view else None
    after = revised.point_of_view.proof if revised.point_of_view else None
    if before is not None and after is None:
        out(
            f"note      the new text no longer quotes proof {before.proof_id} exactly as "
            "written, so review will not show it as a checked proof"
        )
    return revised


def deliver_draft(
    store: Store, *, draft_id: str, sender: Sender | None = None, now: Clock = utc_now
) -> Appended:
    """Deliver the latest version on the scope's platform if it is authorized now; raise otherwise.

    A live scope sends through ``sender`` (ADR 0022).
    """
    draft = store.latest_artifact(draft_id)
    platform = store.scope.execution.platform
    return deliver(store, requested=draft.ref, platform=platform, at=now(), sender=sender)


def resolve_delivery(
    store: Store,
    *,
    draft_id: str,
    found: Literal["sent", "not-sent"],
    reporter: str,
    now: Clock = utc_now,
) -> Appended:
    """Record what a person found in their Sent folder for an unknown send (ADR 0022)."""
    return resolve_send(
        store, artifact_id=draft_id, found=found, by=Actor(kind="human", id=reporter), at=now()
    )


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
