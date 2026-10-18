"""Drafts from the seller's point of view (ADR 0026): a pain, a proof quoted exactly from a
read of its page, the offer, and the run's hiring tally. Synthetic sellers only."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from auggie.acquisition.careers import ROLE_CAP
from auggie.acquisition.fixture import FixtureAcquisition, RecordedListings
from auggie.contracts import Artifact, SubjectRef
from auggie.judgment.rules import RulesQualifier
from auggie.operate import describe_draft
from auggie.pipeline import read_proof_pages, record_revision, research_run
from auggie.proofs import holds
from auggie.store import Backend, Store
from auggie.store.memory import InMemoryBackend
from auggie.strategy import LoadedStrategy, load_strategy
from auggie.synthetic import SyntheticInputs, load_inputs, run_scenario
from tests.conftest import EXAMPLES, mongo_backend, variant

KESTREL = EXAMPLES / "kestrel-security"
INPUTS = load_inputs(KESTREL)
PROOF_PAGE = "https://lanternwatch.example/customers/harborview-freight"
CLAIM = (
    "Harborview Freight cut the time to triage a detection alert from four hours to twenty minutes."
)


@pytest.fixture(params=["memory", "mongodb"])
def backend(request: pytest.FixtureRequest) -> Iterator[Backend]:
    if request.param == "memory":
        yield InMemoryBackend()
    else:
        with mongo_backend() as mongo:
            yield mongo


def strategy(**changes: Any) -> LoadedStrategy:
    loaded = load_strategy(
        KESTREL / "inputs", INPUTS.strategy.path, commit=INPUTS.strategy.commit, scope=INPUTS.scope
    )
    if not changes:
        return loaded
    data = loaded.strategy.model_dump(mode="json")
    data.update(changes)
    return LoadedStrategy(type(loaded.strategy).model_validate(data), loaded.ref)


def research(
    store: Store, inputs: SyntheticInputs = INPUTS, rules: LoadedStrategy | None = None
) -> Any:
    return research_run(
        store,
        strategy=rules or strategy(),
        account=inputs.account,
        acquisition=FixtureAcquisition(inputs.responses),
        qualifier=RulesQualifier(),
        times=inputs.run,
        listings=RecordedListings([inputs.listings[-1]]),
    )


def read_proof(store: Store, rules: LoadedStrategy | None = None) -> None:
    read_proof_pages(
        store,
        strategy=rules or strategy(),
        acquisition=FixtureAcquisition(INPUTS.responses),
        run_id=None,
        now=lambda: datetime(2026, 10, 12, 10, tzinfo=UTC),
    )


# M5's gate: every proof in a draft is a vendor claim with exactly one source, quoted exactly


def test_a_draft_quotes_its_proof_exactly_from_the_one_read_that_holds_it(
    backend: Backend,
) -> None:
    run = run_scenario(KESTREL, backend=backend)
    draft = run.result.draft
    assert draft is not None and draft.point_of_view is not None
    view = draft.point_of_view
    assert (view.play, view.pain) == ("detection-triage", "alert-triage")
    proof = view.proof
    assert proof is not None and proof.claim == CLAIM
    assert f'"{CLAIM}"' in draft.content
    assert str(proof.url) == PROOF_PAGE
    read = run.store.get_evidence(proof.evidence_id)
    assert read.subject == SubjectRef(kind="seller", id="demo-security")
    assert str(read.source_url) == PROOF_PAGE and holds(read.excerpt or "", CLAIM)
    assert proof.evidence_id in draft.evidence_ids
    # A person still approves the exact draft before anything is delivered.
    assert [a.scope for a in run.approvals] == ["content", "delivery"]
    assert all(a.artifact == draft.ref for a in run.approvals)


def test_a_draft_says_what_the_listing_showed_and_never_a_role_or_link() -> None:
    run = run_scenario(KESTREL)
    draft = run.result.draft
    assert draft is not None
    assert "Fernhollow Logistics listed 2 detection engineering roles on 15 October 2026." in (
        draft.content
    )
    assert "Detection Engineer" not in draft.content.replace("detection engineering", "")
    assert "jobs.lever.example" not in draft.content
    assert "a 20-minute walkthrough of how teams like Harborview" in draft.content


def test_a_partial_read_is_quoted_as_a_lower_bound() -> None:
    body = INPUTS.listings[1].body
    assert isinstance(body, list)
    detection = body[:2]  # the two detection roles
    others = [
        {
            "id": f"posting-{n:05d}",
            "text": f"Role {n}",
            "hostedUrl": f"https://jobs.lever.example/f/{n}",
        }
        for n in range(ROLE_CAP - 1)
    ]
    capped = INPUTS.listings[1].model_copy(update={"body": [*detection, *others]})
    inputs = INPUTS.model_copy(update={"listings": (capped,)})
    store = InMemoryBackend().open(INPUTS.scope)
    read_proof(store)
    result = research(store, inputs)
    assert result.snapshot.status == "partial"
    assert "listed at least 2 detection engineering roles on 15 October 2026." in (
        result.draft.content
    )


# Without a usable proof


def test_without_a_usable_proof_the_next_play_is_used() -> None:
    store = InMemoryBackend().open(INPUTS.scope)  # no read of the proof's page
    result = research(store)
    assert result.draft is not None and result.draft.point_of_view is None
    assert result.draft.content.startswith("Subject: Detection coverage while the team is still")
    assert result.draft.evidence_ids == ()


def test_without_a_fallback_the_run_says_why_no_proof_is_usable() -> None:
    rules = strategy()
    only_proof = strategy(
        plays=[p.model_dump(mode="json") for p in rules.strategy.plays if p.pain is not None]
    )
    store = InMemoryBackend().open(INPUTS.scope)
    result = research(store, rules=only_proof)
    assert result.draft is None
    assert result.stopped == (
        "play 'detection-triage' quotes a proof, and none of its pain's proofs is usable: "
        "its page has not been read; read it with proofs --read"
    )


# Review and revision


def test_review_shows_the_pain_and_the_proof_as_the_sellers_own() -> None:
    run = run_scenario(KESTREL)
    assert run.result.draft is not None
    printed: list[str] = []
    describe_draft(run.store, run.result.draft, printed.append)
    text = "\n".join(printed)
    assert (
        "pain      alert-triage, from play detection-triage: an angle, never a fact about the "
        "account"
    ) in text
    assert "          fit: Lanternwatch ranks and groups the alerts" in text
    assert "          limits: Lanternwatch does not write detections" in text
    assert "          metric: Hours from an alert firing to its triage." in text
    assert f'proof     "{CLAIM}"' in text
    assert (
        "          a vendor claim: Harborview Freight (result), "
        f"{PROOF_PAGE}, read 2026-10-12 10:00:00+00:00 (ev-proof-harborview-triage)"
    ) in text
    assert "          caveat: One customer's own measurement" in text


def test_a_revision_keeps_the_point_of_view_it_was_composed_from() -> None:
    undelivered = INPUTS.model_copy(update={"reviews": (), "delivery": None, "outcome": None})
    run = run_scenario(KESTREL, inputs=undelivered)
    draft = run.result.draft
    assert draft is not None
    revised = record_revision(
        run.store,
        previous=draft.ref,
        content=draft.content.replace("Riley\n", "Riley Moss\n"),
        by=INPUTS.strategy.activated_by,
        at=datetime(2026, 10, 16, tzinfo=UTC),
    )
    assert revised.point_of_view == draft.point_of_view


def test_a_draft_must_cite_the_read_of_the_proof_it_quotes() -> None:
    run = run_scenario(KESTREL)
    draft = run.result.draft
    assert draft is not None
    with pytest.raises(ValidationError, match="cites the read of the proof it quotes"):
        Artifact.model_validate(variant(draft, evidence_ids=[]))
    as_json = json.loads(draft.model_dump_json())
    assert as_json["point_of_view"]["proof"]["evidence_id"] == "ev-proof-harborview-triage"
