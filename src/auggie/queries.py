"""Read-only views of one scope's records, for the shell and for coding agents (ADR 0025).

Nothing here writes. Every view is plain data, ready for JSON, and names the
records it rests on and when their sources were read. A view is never read
back into the Store.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from auggie.accounts import Account
from auggie.contracts import CustomerScope, RecordRef, SourceEvidence, SubjectRef
from auggie.contracts.strategy import Strategy
from auggie.pipeline import delivery_state
from auggie.signals import phrase_pattern, quote, stack_matches
from auggie.store import RecordNotFound, Store

View = dict[str, Any]


def load_prospects(folder: Path, scope: CustomerScope) -> tuple[dict[str, Account], list[str]]:
    """The prospect files in ``folder`` that belong to ``scope``, by account ID, and the names of
    files skipped, with why."""
    prospects: dict[str, Account] = {}
    skipped: list[str] = []
    for path in sorted(folder.glob("*.json")):
        try:
            account = Account.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            skipped.append(f"{path.name}: not a prospect file")
            continue
        if account.customer_id != scope.customer_id:
            skipped.append(f"{path.name}: belongs to {account.customer_id}")
            continue
        prospects[account.account_id] = account
    return prospects, skipped


def account_ids(store: Store, prospects: Mapping[str, Account] = {}) -> list[str]:
    """Every account the scope knows: from its records, in the order first seen, then any
    prospect file not researched yet."""
    seen: dict[str, None] = {}
    for event in store.list_events():
        for ref in event.refs:
            if ref.kind == "account":
                seen.setdefault(ref.id, None)
    for evidence in store.list_evidence():
        if evidence.subject.kind == "account":
            seen.setdefault(evidence.subject.id, None)
    for account_id in prospects:
        seen.setdefault(account_id, None)
    return list(seen)


def _decisions(store: Store, account_id: str) -> list[str]:
    account = RecordRef(kind="account", id=account_id)
    return [
        ref.id
        for event in store.list_events(type="qualification.decided")
        if account in event.refs
        for ref in event.refs
        if ref.kind == "qualification"
    ]


def _drafts(store: Store, account_id: str) -> list[View]:
    ids: dict[str, None] = {}
    for event in store.list_events(type="draft.composed"):
        for ref in event.refs:
            if ref.kind == "artifact":
                ids.setdefault(ref.id, None)
    drafts = []
    for artifact_id in ids:
        latest = store.latest_artifact(artifact_id)
        if latest.account_id != account_id:
            continue
        approvals = [a for a in store.list_approvals(artifact_id) if a.artifact == latest.ref]
        state = (
            delivery_state(store, artifact_id, latest.recipient_id) if latest.recipient_id else None
        )
        if state is None or (state.sent is None and not state.attempts):
            delivery = "not delivered"
        elif state.sent is not None:
            delivery = "simulated" if state.sent.type == "delivery.simulated" else "sent"
        elif state.unknown:
            delivery = "unknown: check the Sent folder"
        elif state.closed:
            delivery = "recorded as not sent"
        else:
            delivery = "failed; may be tried again"
        drafts.append(
            {
                "draft_id": artifact_id,
                "version": latest.ref.content_hash,
                "composed_at": latest.composed_at,
                "recipient_id": latest.recipient_id,
                "approvals": [
                    {"scope": a.scope, "decision": a.decision, "by": a.reviewer.id}
                    for a in approvals
                ],
                "delivery": delivery,
            }
        )
    return drafts


def _validation(store: Store) -> dict[str, str]:
    """Each evidence ID's validation, as its events record it."""
    found: dict[str, str] = {}
    for type, word in (("evidence.validated", "admitted"), ("evidence.rejected", "rejected")):
        for event in store.list_events(type=type):  # type: ignore[arg-type]
            for ref in event.refs:
                if ref.kind == "evidence":
                    reason = event.attributes.get("reason")
                    found[ref.id] = f"{word}: {reason}" if reason else word
    return found


def _evidence(store: Store, account_id: str | None) -> list[SourceEvidence]:
    subject = SubjectRef(kind="account", id=account_id) if account_id else None
    return list(store.list_evidence(subject=subject))


def _evidence_row(evidence: SourceEvidence, validation: Mapping[str, str]) -> View:
    return {
        "evidence_id": evidence.evidence_id,
        "account_id": evidence.subject.id,
        "source_url": str(evidence.source_url),
        "status": evidence.status,
        "observed_at": evidence.observed_at,
        "validation": validation.get(evidence.evidence_id, "not checked"),
    }


def accounts_view(store: Store, prospects: Mapping[str, Account] = {}) -> list[View]:
    """One row per account: its latest qualification, its evidence count, and its drafts."""
    rows = []
    for account_id in account_ids(store, prospects):
        decisions = _decisions(store, account_id)
        latest = store.get_qualification(decisions[-1]) if decisions else None
        prospect = prospects.get(account_id)
        rows.append(
            {
                "account_id": account_id,
                "name": prospect.name if prospect else None,
                "qualification": (
                    {
                        "decision_id": latest.decision_id,
                        "outcome": latest.outcome,
                        "decided_at": latest.decided_at,
                    }
                    if latest
                    else None
                ),
                "evidence": len(_evidence(store, account_id)),
                "drafts": _drafts(store, account_id),
            }
        )
    return rows


def brief_view(store: Store, account_id: str, prospects: Mapping[str, Account] = {}) -> View:
    """What the scope holds about one account: the latest judgment with its findings and their
    citations, every piece of evidence, and the drafts."""
    if account_id not in account_ids(store, prospects):
        raise RecordNotFound(f"account {account_id!r}")
    decisions = _decisions(store, account_id)
    decision = store.get_qualification(decisions[-1]) if decisions else None
    prospect = prospects.get(account_id)
    validation = _validation(store)
    return {
        "account_id": account_id,
        "name": prospect.name if prospect else None,
        "qualification": (
            {
                "decision_id": decision.decision_id,
                "outcome": decision.outcome,
                "decided_at": decision.decided_at,
                "strategy": f"{decision.strategy.path}@{decision.strategy.commit[:7]}",
                "findings": [
                    {
                        "criterion": f.criterion,
                        "assessment": f.assessment,
                        "basis": f.basis,
                        "rationale": f.rationale,
                        "evidence_ids": list(f.evidence_ids),
                    }
                    for f in decision.findings
                ],
            }
            if decision
            else None
        ),
        "evidence": [_evidence_row(e, validation) for e in _evidence(store, account_id)],
        "drafts": _drafts(store, account_id),
    }


def find_view(store: Store, words: str, account_id: str | None = None) -> list[View]:
    """Every stored excerpt that holds ``words`` as written, with a quote around the first
    place it does. Searches what was stored; reads nothing new."""
    pattern = phrase_pattern(words)
    rows = []
    for evidence in _evidence(store, account_id):
        match = pattern.search(evidence.excerpt or "")
        if match is not None:
            rows.append(
                {
                    "evidence_id": evidence.evidence_id,
                    "account_id": evidence.subject.id,
                    "source_url": str(evidence.source_url),
                    "observed_at": evidence.observed_at,
                    "quote": quote(evidence.excerpt or "", [match.span()]),
                }
            )
    return rows


def stack_view(store: Store, strategy: Strategy, account_id: str | None = None) -> list[View]:
    """Where admitted evidence names the strategy's technologies. Each row is inference: a name
    on a page, not proof the account runs it (ADR 0023)."""
    stack = strategy.signals.stack
    if stack is None:
        return []
    admitted = {k for k, v in _validation(store).items() if v == "admitted"}
    rows = []
    for evidence in _evidence(store, account_id):
        if evidence.evidence_id not in admitted:
            continue
        for term_id, term in stack.terms.items():
            for match in stack_matches(evidence.excerpt or "", term)[:1]:
                rows.append(
                    {
                        "term": term_id,
                        "label": term.label,
                        "account_id": evidence.subject.id,
                        "evidence_id": evidence.evidence_id,
                        "source_url": str(evidence.source_url),
                        "observed_at": evidence.observed_at,
                        "near": match.near,
                        "quote": match.quote,
                    }
                )
    return rows
