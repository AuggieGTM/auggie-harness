from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from auggie.contracts import Artifact, sha256_digest
from tests.conftest import variant


def edited(draft: Artifact, content: str, **changes: Any) -> dict[str, Any]:
    ref = variant(draft.ref, content_hash=sha256_digest(content))
    return variant(draft, ref=ref, content=content, **changes)


def test_the_fixture_draft_is_a_valid_artifact(draft: Artifact) -> None:
    assert draft.ref.content_hash == sha256_digest(draft.content)
    assert Artifact.model_validate_json(draft.model_dump_json()) == draft


def test_content_must_match_its_hash(draft: Artifact) -> None:
    with pytest.raises(ValidationError, match=r"does not match ref\.content_hash"):
        Artifact.model_validate(variant(draft, content=draft.content + "P.S."))


def test_content_is_kept_byte_exact(draft: Artifact) -> None:
    padded = "  " + draft.content + "\n\n"
    assert Artifact.model_validate(edited(draft, padded)).content == padded


def test_a_draft_needs_a_recipient(draft: Artifact) -> None:
    with pytest.raises(ValidationError, match="a draft needs a recipient"):
        Artifact.model_validate(variant(draft, recipient_id=None))


def test_a_brief_has_no_recipient(draft: Artifact) -> None:
    brief = variant(draft, ref=variant(draft.ref, kind="brief"))
    with pytest.raises(ValidationError, match="only drafts have one"):
        Artifact.model_validate(brief)
    brief.pop("recipient_id")
    assert Artifact.model_validate(brief).recipient_id is None


def test_an_artifact_must_cite_evidence_or_snapshots(draft: Artifact) -> None:
    with pytest.raises(ValidationError, match="cites the evidence or snapshots"):
        Artifact.model_validate(variant(draft, evidence_ids=[]))
    # A draft from a careers listing alone cites its snapshots (ADR 0024).
    listed = Artifact.model_validate(variant(draft, evidence_ids=[], snapshot_ids=["snap-a"]))
    assert listed.snapshot_ids == ("snap-a",)
    with pytest.raises(ValidationError, match="snapshot_ids must not repeat"):
        Artifact.model_validate(variant(draft, snapshot_ids=["snap-a", "snap-a"]))


def test_cited_evidence_must_not_repeat(draft: Artifact) -> None:
    repeated = ["ev-northstar-careers", "ev-northstar-careers"]
    with pytest.raises(ValidationError, match="must not repeat"):
        Artifact.model_validate(variant(draft, evidence_ids=repeated))


def test_an_edit_supersedes_the_previous_version(draft: Artifact) -> None:
    content = draft.content.replace("Would that be useful?", "Worth a look?")
    edit = Artifact.model_validate(
        edited(
            draft,
            content,
            supersedes=draft.ref.content_hash,
            composed_by={"kind": "human", "id": "demo-reviewer"},
        )
    )
    assert edit.ref.artifact_id == draft.ref.artifact_id
    assert edit.ref.content_hash != draft.ref.content_hash


def test_a_version_cannot_supersede_itself(draft: Artifact) -> None:
    with pytest.raises(ValidationError, match="cannot supersede itself"):
        Artifact.model_validate(variant(draft, supersedes=draft.ref.content_hash))


def test_unknown_fields_are_rejected(draft: Artifact) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Artifact.model_validate(variant(draft, approved=True))
