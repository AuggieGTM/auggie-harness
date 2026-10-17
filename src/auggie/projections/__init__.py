"""Read-only workbook views of a customer's Store (ADR 0018)."""

from __future__ import annotations

from collections.abc import Mapping

from auggie.contracts import HumanApproval
from auggie.store import Store

Row = tuple[str, ...]
Tab = tuple[Row, ...]
Workbook = Mapping[str, Tab]

TABS = ("auggie: accounts", "auggie: findings", "auggie: evidence", "auggie: drafts")

HEADERS: tuple[Row, ...] = (
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
)


def _approval(approval: HumanApproval) -> str:
    text = f"{approval.decision} by {approval.reviewer.id} at {approval.decided_at.isoformat()}"
    if approval.scope == "delivery":
        if approval.target is not None:
            text += f" for {approval.target.platform}:{approval.target.recipient_id}"
        if approval.expires_at is not None:
            text += f" until {approval.expires_at.isoformat()}"
    return text


def project(store: Store) -> Workbook:
    """Project records in write order, without copying page text or draft wording."""
    accounts, findings, evidence, drafts = ([header] for header in HEADERS)
    decisions: dict[str, None] = {}
    artifacts: dict[str, None] = {}
    validations: dict[str, str] = {}
    deliveries: dict[str, str] = {}
    outcomes: dict[str, str] = {}
    for event in store.list_events():
        for ref in event.refs:
            if event.type == "qualification.decided" and ref.kind == "qualification":
                decisions[ref.id] = None
            elif event.type == "draft.composed" and ref.kind == "artifact":
                artifacts[ref.id] = None
            elif event.type == "evidence.validated" and ref.kind == "evidence":
                validations[ref.id] = "admitted"
            elif event.type == "evidence.rejected" and ref.kind == "evidence":
                validations[ref.id] = f"rejected: {event.attributes.get('reason') or ''}"
            elif event.type == "delivery.simulated" and ref.kind == "artifact":
                deliveries[ref.id] = event.occurred_at.isoformat()
            elif event.type == "outcome.recorded" and ref.kind == "artifact":
                outcomes[ref.id] = str(event.attributes.get("outcome") or "")

    for decision_id in decisions:
        decision = store.get_qualification(decision_id)
        actor = decision.decided_by
        accounts.append(
            (
                decision.account_id,
                decision.outcome,
                decision.decided_at.isoformat(),
                decision_id,
                f"{decision.strategy.path}@{decision.strategy.commit[:7]}",
                "; ".join(f.criterion for f in decision.findings if f.assessment == "meets"),
                "; ".join(decision.unknowns),
                f"{actor.id} {actor.version}" if actor.version else actor.id,
            )
        )
        for finding in decision.findings:
            findings.append(
                (
                    decision.account_id,
                    decision_id,
                    finding.criterion,
                    finding.assessment,
                    finding.basis,
                    finding.rationale,
                    "; ".join(finding.evidence_ids),
                    "; ".join(
                        str(store.get_evidence(eid).source_url) for eid in finding.evidence_ids
                    ),
                )
            )

    for record in store.list_evidence():
        evidence.append(
            (
                record.evidence_id,
                f"{record.subject.kind}:{record.subject.id}",
                str(record.source_url),
                record.status,
                record.observed_at.isoformat(),
                record.published_at.isoformat() if record.published_at else "",
                validations.get(record.evidence_id, ""),
                record.failure_reason or "",
                record.provider_request_id or "",
                record.acquired_via.id,
            )
        )

    for artifact_id in artifacts:
        latest = store.latest_artifact(artifact_id)
        version = latest
        count = 1
        while version.supersedes is not None:
            version = store.get_artifact(
                version.ref.model_copy(update={"content_hash": version.supersedes})
            )
            count += 1
        approvals = [a for a in store.list_approvals(artifact_id) if a.artifact == latest.ref]
        drafts.append(
            (
                artifact_id,
                latest.account_id,
                latest.recipient_id or "",
                str(count),
                latest.ref.content_hash.removeprefix("sha256:")[:12],
                latest.composed_at.isoformat(),
                "; ".join(_approval(a) for a in approvals if a.scope == "content"),
                "; ".join(_approval(a) for a in approvals if a.scope == "delivery"),
                deliveries.get(artifact_id, ""),
                outcomes.get(artifact_id, ""),
            )
        )
    return dict(
        zip(TABS, (tuple(accounts), tuple(findings), tuple(evidence), tuple(drafts)), strict=True)
    )
