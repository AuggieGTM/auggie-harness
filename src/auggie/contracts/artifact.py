"""Artifacts: one exact version of a brief or draft, with what it was composed from (ADR 0014)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, HttpUrl, StringConstraints, model_validator

from auggie.contracts.approval import ArtifactRef
from auggie.contracts.common import (
    Actor,
    Contract,
    Digest,
    RecordId,
    Slug,
    StrategyRef,
    Text,
    UtcDatetime,
    sha256_digest,
)
from auggie.contracts.strategy import Claim, Copy, Label

Passage = Annotated[str, StringConstraints(min_length=1, max_length=20_000)]
"""One template line as rendered. Approved copy filled into it may add line breaks."""

Content = Annotated[str, StringConstraints(min_length=1, max_length=50_000)]
"""Exact artifact text. Never stripped or normalized: its bytes are what approvals bind to."""


class QuotedProof(Contract):
    """The proof a draft quotes: a vendor claim, and the read of its page that holds it."""

    proof_id: Slug
    customer: Label
    claim: Claim
    url: HttpUrl
    kind: Literal["result", "scale"]
    caveat: Text | None = None
    evidence_id: RecordId
    """The read of the proof's page, about the seller, that holds the claim word for word."""
    passages: Annotated[tuple[Passage, ...], Field(min_length=1)]
    """Each template line that renders the claim or the customer, exactly as rendered, and so
    whole lines of the draft. The proof is bound to them: an edit to any of them unbinds it."""

    @model_validator(mode="after")
    def _passages_render_it(self) -> QuotedProof:
        if not all(self.claim in p or self.customer in p for p in self.passages):
            raise ValueError("each passage of a quoted proof renders its claim or customer")
        return self

    def held_by(self, content: str) -> bool:
        """Whether every passage is in ``content`` unchanged, as whole lines, each its own
        occurrence and in the order rendered, so one surviving copy never stands for two.
        Lines are split as the passages were, so any line ending holds."""
        lines = content.splitlines()
        start = 0
        for passage in self.passages:
            block = passage.splitlines()
            found = next(
                (n for n in range(start, len(lines)) if lines[n : n + len(block)] == block), None
            )
            if found is None:
                return False
            start = found + len(block)
        return True


class PointOfView(Contract):
    """The play and pain a draft was composed from, with what review shows beside the draft:
    where the seller fits and stops, and the optional metric (ADR 0026). Kept on the draft,
    since review reads the Store and not the strategy."""

    play: Slug
    pain: Slug
    fit: Copy
    limits: Copy
    metric: Copy | None = None
    threshold: Copy | None = None
    consequence: Copy | None = None
    proof: QuotedProof | None = None


class Artifact(Contract):
    """One version of a brief or draft.

    An edit is a new version with the same ``artifact_id``, a new hash, and
    ``supersedes`` naming the hash it replaces. Approvals bind to one hash, so
    an edit voids every earlier approval of the artifact.

    A version cites the page evidence (``evidence_ids``) and careers snapshots
    (``snapshot_ids``, ADR 0024) it was composed from: at least one of either.
    """

    schema_version: Literal[1] = 1
    customer_id: Slug
    ref: ArtifactRef
    media_type: Literal["text/markdown"] = "text/markdown"
    content: Content
    account_id: RecordId
    recipient_id: RecordId | None = None
    qualification_id: RecordId
    evidence_ids: tuple[RecordId, ...] = ()
    snapshot_ids: tuple[RecordId, ...] = ()
    strategy: StrategyRef
    composed_by: Actor
    composed_at: UtcDatetime
    supersedes: Digest | None = None
    play: Slug | None = None
    """The play it was composed from (ADR 0028); drafts from before then have none."""
    point_of_view: PointOfView | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> Artifact:
        if sha256_digest(self.content) != self.ref.content_hash:
            raise ValueError("content does not match ref.content_hash")
        if (self.ref.kind == "draft") != (self.recipient_id is not None):
            raise ValueError("a draft needs a recipient, and only drafts have one")
        if not self.evidence_ids and not self.snapshot_ids:
            raise ValueError("an artifact cites the evidence or snapshots it was composed from")
        for name, ids in (("evidence_ids", self.evidence_ids), ("snapshot_ids", self.snapshot_ids)):
            if len(set(ids)) != len(ids):
                raise ValueError(f"{name} must not repeat")
        if self.supersedes == self.ref.content_hash:
            raise ValueError("a version cannot supersede itself")
        proof = self.point_of_view.proof if self.point_of_view else None
        if proof is not None:
            if proof.evidence_id not in self.evidence_ids:
                raise ValueError("a draft cites the read of the proof it quotes")
            if not proof.held_by(self.content):
                raise ValueError("a draft holds the proof it quotes exactly as written")
        return self
