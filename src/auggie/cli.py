"""Operator commands. ``auggie run <scenario>`` runs a synthetic scenario and reports each stage."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from auggie.contracts import (
    Artifact,
    CustomerScope,
    QualificationDecision,
    ScopeViolation,
    sha256_digest,
)
from auggie.contracts.qualification import CitationError
from auggie.delivery import DeliveryBlocked
from auggie.pipeline import deliver
from auggie.store import Backend, Store, StoreError
from auggie.store.memory import InMemoryBackend
from auggie.synthetic import ScenarioRun, load_inputs, run_scenario

if TYPE_CHECKING:
    from auggie.store.mongodb import MongoBackend

Print = Callable[[str], None]


def _short(digest: str) -> str:
    return digest.removeprefix("sha256:")[:12]


def report(run: ScenarioRun, out: Print, *, show_draft: bool = False) -> None:
    store = run.store
    ref = run.strategy.ref
    activated = run.activation.event
    out(f"define    {run.strategy.strategy.strategy_id} activated by {activated.actor.id}")
    out(f"          {ref.path} @ {ref.commit[:7]}, sha256 {_short(ref.digest)}")
    for verdict in run.result.verdicts:
        e = verdict.evidence
        detail = f" ({e.failure_reason})" if e.failure_reason else ""
        out(f"observe   {e.evidence_id:<24} {e.status:<9} {e.source_url}{detail}")
    for verdict in run.result.verdicts:
        e = verdict.evidence
        status = "admitted" if verdict.admitted else f"rejected: {verdict.rejected_for}"
        out(f"validate  {e.evidence_id:<24} {status}")
    _report_decision(run.result.decision, out)
    draft = run.result.draft
    if draft is None:
        out(f"compose   no draft: {run.result.stopped}")
        return
    out(
        f"compose   {draft.ref.artifact_id} sha256 {_short(draft.ref.content_hash)} "
        f"for {draft.recipient_id}, citing {', '.join(draft.evidence_ids)}"
    )
    if show_draft:
        out("")
        for line in draft.content.splitlines():
            out(f"          | {line}")
        out("")
    for approval in run.approvals:
        target = (
            f" for {approval.target.platform}:{approval.target.recipient_id}"
            if approval.target
            else ""
        )
        until = f" until {approval.expires_at.isoformat()}" if approval.expires_at else ""
        out(
            f"approve   {approval.scope:<9} {approval.decision} by {approval.reviewer.id}"
            f"{target}{until} ({approval.approval_id})"
        )
    if run.delivery is not None:
        event = run.delivery.event
        person = next(r.id for r in event.refs if r.kind == "person")
        under = next(r.id for r in event.refs if r.kind == "approval")
        again = "" if run.delivery.created else " (already delivered; nothing sent again)"
        out(f"deliver   simulated hand-off to {person}, authorized by {under}{again}")
    if run.outcome is not None:
        event = run.outcome.event
        out(f"learn     outcome {event.attributes['outcome']} reported by {event.actor.id}")
    out("")
    out(f"{len(store.list_events())} events in the {store.scope.customer_id} store.")


def _report_decision(decision: QualificationDecision, out: Print) -> None:
    out(
        f"judge     {decision.account_id} {decision.outcome} by "
        f"{decision.decided_by.id} {decision.decided_by.version or ''}".rstrip()
    )
    for finding in decision.findings:
        cites = f" [{', '.join(finding.evidence_ids)}]" if finding.evidence_ids else ""
        out(
            f"          {finding.assessment:<13} {finding.criterion:<25} "
            f"{finding.basis}: {finding.rationale}{cites}"
        )
    for unknown in decision.unknowns:
        out(f"          unknown: {unknown}")


def _negative_paths(directory: Path) -> list[tuple[str, Callable[[], object]]]:
    """Attempts the slice must refuse. Each callable should raise."""
    inputs = load_inputs(directory)

    def until_review(*scopes: str) -> tuple[InMemoryBackend, ScenarioRun]:
        backend = InMemoryBackend()
        reviews = tuple(r for r in inputs.reviews if r.scope in scopes)
        chosen = inputs.model_copy(update={"reviews": reviews, "delivery": None, "outcome": None})
        return backend, run_scenario(directory, backend=backend, inputs=chosen)

    def draft(run: ScenarioRun) -> Artifact:
        if run.result.draft is None:
            raise RuntimeError(f"the scenario composed no draft: {run.result.stopped}")
        return run.result.draft

    def edited(store: Store, v1: Artifact) -> Artifact:
        content = v1.content + "\nP.S. Edited after approval.\n"
        ref = v1.ref.model_copy(update={"content_hash": sha256_digest(content)})
        v2 = v1.model_copy(
            update={"ref": ref, "content": content, "supersedes": v1.ref.content_hash}
        )
        v2 = Artifact.model_validate(v2.model_dump())
        store.put_artifact(v2)
        return v2

    at = inputs.delivery.requested_at if inputs.delivery else inputs.run.composed_at
    other = CustomerScope(
        customer_id="demo-other",
        synthetic=True,
        data_namespace="auggie_demo_other",
        credential_namespace="demo-other",
        knowledge_namespace="demo-other-strategy",
    )

    def cite_failed_source() -> object:
        _, run = until_review()
        failed = [v.evidence.evidence_id for v in run.result.verdicts if not v.admitted]
        finding = run.result.decision.findings[0].model_copy(update={"evidence_ids": tuple(failed)})
        decision = run.result.decision.model_copy(
            update={"decision_id": "qual-negative", "findings": (finding,)}
        )
        run.store.put_qualification(QualificationDecision.model_validate(decision.model_dump()))
        return None

    def deliver_edited_draft() -> object:
        _, run = until_review("content", "delivery")
        v2 = edited(run.store, draft(run))
        return deliver(run.store, requested=v2.ref, platform="simulated", at=at)

    def deliver_superseded_original() -> object:
        _, run = until_review("content", "delivery")
        v1 = draft(run)
        edited(run.store, v1)
        return deliver(run.store, requested=v1.ref, platform="simulated", at=at)

    def deliver_with_content_approval_only() -> object:
        _, run = until_review("content")
        return deliver(run.store, requested=draft(run).ref, platform="simulated", at=at)

    def deliver_from_another_scope() -> object:
        backend, run = until_review("content", "delivery")
        foreign = backend.open(other)
        return deliver(foreign, requested=draft(run).ref, platform="simulated", at=at)

    def write_into_another_scope() -> object:
        backend, run = until_review()
        backend.open(other).put_evidence(run.result.verdicts[0].evidence)
        return None

    return [
        ("a decision citing the failed source", cite_failed_source),
        ("delivering a draft edited after approval", deliver_edited_draft),
        ("delivering the original after it was edited", deliver_superseded_original),
        ("delivering with content approval only", deliver_with_content_approval_only),
        ("delivering the draft from another customer's scope", deliver_from_another_scope),
        ("writing a record into another customer's scope", write_into_another_scope),
    ]


BLOCKING = (CitationError, DeliveryBlocked, ScopeViolation, StoreError)


def check_negative_paths(directory: Path, out: Print) -> bool:
    """Try each path the slice must refuse. Returns whether every one was blocked."""
    all_blocked = True
    for title, attempt in _negative_paths(directory):
        try:
            attempt()
        except BLOCKING as error:
            out(f"blocked   {title}")
            out(f"          {type(error).__name__}: {error}")
        else:
            all_blocked = False
            out(f"NOT BLOCKED  {title}")
    return all_blocked


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="auggie", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="run a synthetic scenario from its inputs")
    run.add_argument("scenario", type=Path, help="scenario directory, containing inputs/")
    run.add_argument("--show-draft", action="store_true", help="print the composed draft")
    run.add_argument(
        "--negative", action="store_true", help="also try the paths that must be blocked"
    )
    run.add_argument(
        "--mongodb", metavar="URI", help="keep records in MongoDB at URI instead of in memory"
    )
    run.add_argument(
        "--database-prefix",
        default="",
        metavar="PREFIX",
        help="prefix for MongoDB database names, to keep demo and test runs apart",
    )
    args = parser.parse_args(argv)

    out: Print = print
    stops: tuple[type[Exception], ...] = (*BLOCKING, ValueError, OSError)
    backend: Backend = InMemoryBackend()
    mongo: MongoBackend | None = None
    where = "in-memory store"
    if args.mongodb:
        try:
            from pymongo import MongoClient
            from pymongo.errors import PyMongoError

            from auggie.store.mongodb import MongoBackend
        except ImportError:
            out("stopped   MongoDB support needs the extra: auggie-harness[mongodb]")
            return 1
        client: MongoClient[dict[str, Any]] = MongoClient(
            args.mongodb, serverSelectionTimeoutMS=5000
        )
        backend = mongo = MongoBackend(client, database_prefix=args.database_prefix)
        stops = (*stops, PyMongoError)
        where = "MongoDB store"

    out(f"{args.scenario}: synthetic run, {where}, no provider calls, simulated delivery only")
    out("")
    try:
        result = run_scenario(args.scenario, backend=backend)
    except stops as error:
        out(f"stopped   {type(error).__name__}: {error}")
        return 1
    report(result, out, show_draft=args.show_draft)
    if mongo is not None:
        out(f"Stored in MongoDB database {mongo.database_name(result.store.scope)}.")
    if args.negative:
        out("")
        if not check_negative_paths(args.scenario, out):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
