"""Fill a strategy play's template from admitted evidence and the account's records."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime

from auggie.accounts import Account
from auggie.acquisition.validation import Verdict
from auggie.contracts import (
    Actor,
    Artifact,
    ArtifactRef,
    Play,
    QualificationDecision,
    ScopeViolation,
    sha256_digest,
)
from auggie.strategy import LoadedStrategy

COMPOSER = Actor(kind="service", id="fixture-composer", version="0")


class ComposeBlocked(Exception):
    """A draft cannot be written without claiming something the evidence does not show."""


def applies(play: Play, decision: QualificationDecision) -> bool:
    """Whether the account qualified and every criterion the play needs was met."""
    met = {f.criterion for f in decision.findings if f.assessment == "meets"}
    return decision.outcome == "qualified" and set(play.requires) <= met


def select_play(strategy: LoadedStrategy, decision: QualificationDecision) -> Play | None:
    """The first play in the strategy that applies to ``decision``."""
    return next((play for play in strategy.strategy.plays if applies(play, decision)), None)


def compose_draft(
    *,
    artifact_id: str,
    play: Play,
    strategy: LoadedStrategy,
    decision: QualificationDecision,
    account: Account,
    sources: Mapping[str, Verdict],
    at: datetime,
) -> Artifact:
    """Render ``play`` for ``account``. Every field must come from admitted evidence."""
    for owner in (account.customer_id, strategy.strategy.customer_id):
        if owner != decision.customer_id:
            raise ScopeViolation(f"input belongs to {owner!r}, not {decision.customer_id!r}")
    if not applies(play, decision):
        raise ComposeBlocked(f"play {play.id!r} does not apply to {decision.decision_id!r}")
    findings = {f.criterion: f for f in decision.findings}
    used = {eid for name in play.requires for eid in findings[name].evidence_ids}
    values = {"first_name": account.recipient.first_name, "account_name": account.name}
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
    content = play.template.format_map(values)
    in_source_order = [
        v.evidence.evidence_id for v in sources.values() if v.evidence.evidence_id in used
    ]
    return Artifact(
        customer_id=decision.customer_id,
        ref=ArtifactRef(artifact_id=artifact_id, kind="draft", content_hash=sha256_digest(content)),
        content=content,
        account_id=account.account_id,
        recipient_id=account.recipient.person_id,
        qualification_id=decision.decision_id,
        evidence_ids=tuple(in_source_order),
        strategy=strategy.ref,
        composed_by=COMPOSER,
        composed_at=at,
    )
