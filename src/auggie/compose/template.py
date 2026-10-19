"""Fill a strategy play's template from admitted evidence and the account's records."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from auggie.accounts import Account
from auggie.acquisition.validation import Verdict
from auggie.contracts import (
    Actor,
    Artifact,
    ArtifactRef,
    Play,
    PointOfView,
    QualificationDecision,
    QuotedProof,
    ScopeViolation,
    sha256_digest,
)
from auggie.contracts.strategy import (
    PROOF_FIELDS,
    FamilyOpenings,
    Strategy,
    template_fields,
)
from auggie.judgment import ListingReadings
from auggie.proofs import ProofCheck, page_problem
from auggie.signals.tally import in_family
from auggie.strategy import LoadedStrategy

COMPOSER = Actor(kind="service", id="fixture-composer", version="0")


class ComposeBlocked(Exception):
    """A draft cannot be written without claiming something the evidence does not show."""


def applies(play: Play, decision: QualificationDecision) -> bool:
    """Whether the account qualified and every criterion the play needs was met."""
    met = {f.criterion for f in decision.findings if f.assessment == "meets"}
    return decision.outcome == "qualified" and set(play.requires) <= met


def proof_for(
    play: Play, strategy: Strategy, proofs: Mapping[str, ProofCheck]
) -> ProofCheck | None:
    """The first usable proof of the play's pain, in the pain's order (ADR 0026)."""
    if play.pain is None:
        return None
    usable = (proofs.get(p) for p in strategy.pains[play.pain].proofs)
    return next((check for check in usable if check is not None and check.usable), None)


def select_play(
    strategy: LoadedStrategy,
    decision: QualificationDecision,
    proofs: Mapping[str, ProofCheck] = {},
) -> Play | None:
    """The first play in the strategy that applies to ``decision`` and, if it quotes a proof,
    has a usable one."""
    rules = strategy.strategy
    return next(
        (
            play
            for play in rules.plays
            if applies(play, decision)
            and (not play.uses_proof or proof_for(play, rules, proofs) is not None)
        ),
        None,
    )


def _hiring_values(
    play: Play,
    strategy: Strategy,
    decision: QualificationDecision,
    account: Account,
    listing: ListingReadings | None,
) -> dict[str, str]:
    """The tally of this run's read of the listing, for a play that names a hiring criterion:
    the count, the family's label and the read date. Never a role's title or link. The read
    must be the account's, and the one the decision's finding for that criterion cites."""
    if play.hiring is None:
        return {}
    criterion = strategy.criterion(play.hiring)
    hiring = strategy.signals.hiring
    if listing is None or hiring is None or not isinstance(criterion, FamilyOpenings):
        raise ComposeBlocked(f"play {play.id!r} needs this run's read of the careers listing")
    read = listing.current
    finding = next(f for f in decision.findings if f.criterion == play.hiring)
    if (
        read.customer_id != decision.customer_id
        or read.account_id != account.account_id
        or read.snapshot_id not in finding.snapshot_ids
        or finding.assessment != "meets"
    ):
        raise ComposeBlocked(
            f"the listing read given is not the one finding {play.hiring!r} cites as met"
        )
    current, _ = listing.readings()
    count = len(in_family(current, criterion.family, hiring))
    return {
        "hiring_count": f"at least {count}" if read.status == "partial" else str(count),
        "hiring_family": hiring.families[criterion.family].label,
        "hiring_date": f"{read.read_at.day} {read.read_at:%B %Y}",
    }


def template_passages(play: Play, values: Mapping[str, str]) -> tuple[str, ...]:
    """Each template line that renders a proof field, exactly as rendered."""
    lines = [line for line in play.template.splitlines() if template_fields(line) & PROOF_FIELDS]
    return tuple(line.format_map(values) for line in lines)


def point_of_view(
    play: Play, strategy: Strategy, quoted: ProofCheck | None, passages: tuple[str, ...]
) -> PointOfView | None:
    """What review shows beside the draft. A quoted proof is bound to ``passages``, the
    draft's lines that quote it, exactly as written, so an edit to any of them unbinds it."""
    if play.pain is None:
        return None
    pain = strategy.pains[play.pain]
    proof = None
    if quoted is not None and quoted.read is not None:
        proof = QuotedProof(
            proof_id=quoted.proof_id,
            customer=quoted.proof.customer,
            claim=quoted.proof.claim,
            url=quoted.proof.url,
            kind=quoted.proof.kind,
            caveat=quoted.proof.caveat,
            evidence_id=quoted.read.evidence_id,
            passages=passages,
        )
    return PointOfView(
        play=play.id,
        pain=play.pain,
        fit=pain.fit,
        limits=pain.limits,
        metric=pain.metric,
        threshold=pain.threshold,
        consequence=pain.consequence,
        proof=proof,
    )


@dataclass(frozen=True)
class Filled:
    """What a play's template is filled from: its fields' values, the evidence and snapshots
    they came from, and the proof it quotes, each checked as ``compose_draft`` requires."""

    values: Mapping[str, str]
    used: frozenset[str]
    snapshots: tuple[str, ...]
    quoted: ProofCheck | None


def fill(
    *,
    play: Play,
    strategy: LoadedStrategy,
    decision: QualificationDecision,
    account: Account,
    sources: Mapping[str, Verdict],
    listing: ListingReadings | None = None,
    proofs: Mapping[str, ProofCheck] = {},
) -> Filled:
    """The values of ``play``'s fields for ``account``. Every one must come from admitted
    evidence, the run's tally, or the strategy's approved copy and a proof whose page read
    holds it; ``ComposeBlocked`` says which cannot."""
    for owner in (account.customer_id, strategy.strategy.customer_id):
        if owner != decision.customer_id:
            raise ScopeViolation(f"input belongs to {owner!r}, not {decision.customer_id!r}")
    if not applies(play, decision):
        raise ComposeBlocked(f"play {play.id!r} does not apply to {decision.decision_id!r}")
    findings = {f.criterion: f for f in decision.findings}
    used = {eid for name in play.requires for eid in findings[name].evidence_ids}
    snapshots = [sid for name in play.requires for sid in findings[name].snapshot_ids]
    values = {"first_name": account.recipient.first_name, "account_name": account.name}
    rules = strategy.strategy
    values.update(_hiring_values(play, rules, decision, account, listing))
    quoted: ProofCheck | None = None
    if play.pain is not None:
        pain = rules.pains[play.pain]
        values.update(pain=pain.statement, offer=pain.offer)
        check = proof_for(play, rules, proofs)
        if play.uses_proof:
            if check is None or check.read is None:
                raise ComposeBlocked(f"play {play.id!r} quotes a proof, and none is usable")
            proof = check.proof
            # The check must be of the strategy's own proof, and its read must still hold it.
            if rules.proofs.by_id.get(check.proof_id) != proof:
                raise ComposeBlocked(f"the check given is not of proof {check.proof_id!r}")
            problem = page_problem(
                check.read,
                customer_id=decision.customer_id,
                url=str(proof.url),
                claim=proof.claim,
                customer=proof.customer,
            )
            if problem is not None:
                raise ComposeBlocked(f"proof {check.proof_id!r}: {problem}")
            values.update(proof_claim=proof.claim, proof_customer=proof.customer)
            quoted = check
    for name, field in play.fields.items():
        label = strategy.strategy.sources[field.source].label
        verdict = sources.get(field.source)
        if verdict is None or not verdict.admitted:
            raise ComposeBlocked(f"field {name!r} needs the {label}, which was not admitted")
        match = re.search(field.pattern, verdict.evidence.excerpt or "")
        if match is None:
            raise ComposeBlocked(f"field {name!r} is not found in the {label}")
        value = match.group(1)
        if value is None or not value.strip():
            raise ComposeBlocked(f"field {name!r} is empty in the {label}")
        values[name] = value
        used.add(verdict.evidence.evidence_id)
    return Filled(values, frozenset(used), tuple(dict.fromkeys(snapshots)), quoted)


def draft_of(
    *,
    artifact_id: str,
    content: str,
    play: Play,
    strategy: LoadedStrategy,
    decision: QualificationDecision,
    account: Account,
    sources: Mapping[str, Verdict],
    filled: Filled,
    passages: tuple[str, ...],
    composed_by: Actor,
    at: datetime,
    fallback: str | None = None,
) -> Artifact:
    """The draft ``content`` of ``play``, citing what ``filled`` came from, in source order."""
    view = point_of_view(play, strategy.strategy, filled.quoted, passages)
    in_source_order = [
        v.evidence.evidence_id for v in sources.values() if v.evidence.evidence_id in filled.used
    ]
    if view is not None and view.proof is not None:
        in_source_order.append(view.proof.evidence_id)
    return Artifact(
        customer_id=decision.customer_id,
        ref=ArtifactRef(artifact_id=artifact_id, kind="draft", content_hash=sha256_digest(content)),
        content=content,
        account_id=account.account_id,
        recipient_id=account.recipient.person_id,
        qualification_id=decision.decision_id,
        evidence_ids=tuple(in_source_order),
        snapshot_ids=filled.snapshots,
        strategy=strategy.ref,
        composed_by=composed_by,
        composed_at=at,
        play=play.id,
        point_of_view=view,
        fallback=fallback,
    )


def compose_draft(
    *,
    artifact_id: str,
    play: Play,
    strategy: LoadedStrategy,
    decision: QualificationDecision,
    account: Account,
    sources: Mapping[str, Verdict],
    at: datetime,
    listing: ListingReadings | None = None,
    proofs: Mapping[str, ProofCheck] = {},
) -> Artifact:
    """Render ``play`` for ``account``. Every field must come from admitted evidence, the
    run's tally, or the strategy's approved copy and a proof whose page read holds it."""
    filled = fill(
        play=play,
        strategy=strategy,
        decision=decision,
        account=account,
        sources=sources,
        listing=listing,
        proofs=proofs,
    )
    return draft_of(
        artifact_id=artifact_id,
        content=play.template.format_map(filled.values),
        play=play,
        strategy=strategy,
        decision=decision,
        account=account,
        sources=sources,
        filled=filled,
        passages=template_passages(play, filled.values),
        composed_by=COMPOSER,
        at=at,
    )
