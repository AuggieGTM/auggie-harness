"""The seller's point of view in the strategy: proofs, pains and the plays that use them
(ADR 0026). Synthetic sellers only."""

from __future__ import annotations

import copy
import pickle
from typing import Any

import pytest
from pydantic import ValidationError

from auggie.contracts import (
    SourceEvidence,
    Strategy,
    SubjectRef,
    sha256_digest,
    verify_cited_evidence,
)
from auggie.contracts.qualification import CitationError
from tests.test_signal_vocabulary import security_strategy

PROOF: dict[str, Any] = {
    "customer": "Harborview Freight",
    "url": "https://lanternwatch.example/customers/harborview-freight",
    "claim": "Harborview Freight cut triage from four hours to twenty minutes.",
    "kind": "result",
}
PAIN: dict[str, Any] = {
    "statement": "New teams triage alerts by hand.",
    "fit": "We rank alerts.",
    "limits": "We do not write detections.",
    "offer": "a short walkthrough",
    "proofs": ["harborview"],
}
TEMPLATE = (
    "Hi {first_name}, {account_name} listed {hiring_count} {hiring_family} roles on "
    '{hiring_date}. {pain} {proof_customer}: "{proof_claim}" Would {offer} help?\n'
)


def security(**changes: Any) -> Strategy:
    """The security seller's strategy, with a play that uses its pain and the hiring tally."""
    data = security_strategy().model_dump(mode="json")
    data["proofs"] = {"max_age_days": 90, "harborview": PROOF}
    data["pains"] = {"alert-triage": PAIN}
    data["plays"] = [
        {
            "id": "with-proof",
            "requires": ["detection-hiring"],
            "pain": "alert-triage",
            "hiring": "detection-hiring",
            "template": TEMPLATE,
        }
    ]
    for key, value in changes.items():
        data[key] = value
    return Strategy.model_validate(data)


def with_play(**changes: Any) -> Strategy:
    play = {
        "id": "with-proof",
        "requires": ["detection-hiring"],
        "pain": "alert-triage",
        "hiring": "detection-hiring",
        "template": TEMPLATE,
        **changes,
    }
    return security(plays=[{k: v for k, v in play.items() if v is not None}])


def test_the_second_seller_holds_a_pain_and_a_proof() -> None:
    strategy = security_strategy()
    assert strategy.proofs.max_age_days == 180
    proof = strategy.proofs.by_id["harborview-triage"]
    assert proof.kind == "result" and proof.caveat is not None
    assert str(proof.url).startswith("https://lanternwatch.example/")
    pain = strategy.pains["alert-triage"]
    assert pain.proofs == ("harborview-triage",)
    assert pain.metric and pain.threshold and pain.consequence


def test_a_play_with_a_pain_and_a_hiring_criterion_may_use_their_fields() -> None:
    strategy = security()
    (play,) = strategy.plays
    assert play.uses_proof and (play.pain, play.hiring) == ("alert-triage", "detection-hiring")
    assert strategy.proofs.max_age_days == 90
    # The file form and the stored form load to the same strategy.
    assert Strategy.model_validate(strategy.model_dump()) == strategy
    assert pickle.loads(pickle.dumps(strategy)) == strategy
    assert copy.deepcopy(strategy) == strategy


def test_older_strategies_without_a_point_of_view_still_load() -> None:
    data = security_strategy().model_dump(mode="json")
    del data["proofs"], data["pains"]
    data["plays"] = [p for p in data["plays"] if p["pain"] is None]
    for play in data["plays"]:
        del play["pain"], play["hiring"]
    strategy = Strategy.model_validate(data)
    assert strategy.proofs.by_id == {} and strategy.pains == {}
    assert strategy.proofs.max_age_days == 180


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"pain": None}, "uses undeclared fields"),
        ({"hiring": None}, "uses undeclared fields"),
        ({"pain": "unknown-pain"}, "names undefined pain 'unknown-pain'"),
        ({"hiring": "names-beaconlog", "requires": ["detection-hiring"]}, "must be required"),
        (
            {"hiring": "detection-openings-new", "requires": ["detection-openings-new"]},
            "not a hiring_family",
        ),
        (
            {"fields": {"offer": {"source": "security", "pattern": "(x)"}}},
            r"declares reserved fields: \['offer'\]",
        ),
    ],
    ids=[
        "pain-fields-need-a-pain",
        "hiring-fields-need-a-criterion",
        "unknown-pain",
        "hiring-not-required",
        "hiring-not-a-count",
        "reserved-field",
    ],
)
def test_a_play_uses_only_the_fields_it_has(changes: dict[str, Any], problem: str) -> None:
    with pytest.raises(ValidationError, match=problem):
        with_play(**changes)


def test_a_play_that_quotes_a_proof_needs_a_pain_with_one() -> None:
    with pytest.raises(ValidationError, match="quotes a proof, but pain 'alert-triage' has none"):
        security(pains={"alert-triage": {**PAIN, "proofs": []}})
    # A pain without proofs still gives a play its statement and offer.
    plain = "Hi {first_name}. {pain} Would {offer} help?\n"
    strategy = security(
        pains={"alert-triage": {**PAIN, "proofs": []}},
        plays=[
            {
                "id": "no-proof",
                "requires": ["detection-hiring"],
                "pain": "alert-triage",
                "template": plain,
            }
        ],
    )
    assert not strategy.plays[0].uses_proof


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"pains": {"alert-triage": {**PAIN, "proofs": ["missing"]}}}, "undefined proof 'missing'"),
        (
            {"pains": {"alert-triage": {**PAIN, "proofs": ["harborview", "harborview"]}}},
            "lists each proof once",
        ),
        ({"pains": {"alert-triage": {**PAIN, "fit": "   "}}}, "cannot be blank"),
        ({"pains": {"alert-triage": {**PAIN, "surprise": "x"}}}, "Extra inputs"),
        ({"proofs": {"harborview": {**PROOF, "kind": "rumour"}}}, "result"),
        ({"proofs": {"harborview": {**PROOF, "url": "ftp://lanternwatch.example/x"}}}, "URL"),
        ({"proofs": {"harborview": {**PROOF, "customer": " "}}}, "cannot be blank"),
        ({"proofs": {"max_age_days": 0, "harborview": PROOF}}, "greater than or equal to 1"),
        ({"proofs": {"Harbor View": PROOF}}, "String should match pattern"),
    ],
    ids=[
        "unknown-proof",
        "repeated-proof",
        "blank-copy",
        "unknown-key",
        "unknown-kind",
        "not-a-web-page",
        "blank-customer",
        "no-age",
        "not-a-slug",
    ],
)
def test_pains_and_proofs_are_checked_when_the_strategy_loads(
    changes: dict[str, Any], problem: str
) -> None:
    with pytest.raises(ValidationError, match=problem):
        security(**changes)


@pytest.mark.parametrize(
    "claim",
    [
        " Harborview cut triage time.",
        "Harborview cut triage time. ",
        "Harborview cut  triage time.",
        "Harborview cut\ntriage time.",
        "Harborview cut\ttriage time.",
        "",
    ],
    ids=["leading-space", "trailing-space", "double-space", "newline", "tab", "empty"],
)
def test_a_claim_is_one_single_spaced_line_as_a_draft_quotes_it(claim: str) -> None:
    with pytest.raises(ValidationError):
        security(proofs={"harborview": {**PROOF, "claim": claim}})


def test_evidence_about_the_seller_supports_only_its_own_scope() -> None:
    def page(customer_id: str, seller: str) -> SourceEvidence:
        excerpt = "Harborview Freight cut triage from four hours to twenty minutes."
        return SourceEvidence.model_validate(
            {
                "evidence_id": f"ev-proof-{seller}",
                "customer_id": customer_id,
                "subject": SubjectRef(kind="seller", id=seller).model_dump(),
                "source_url": PROOF["url"],
                "acquired_via": {"kind": "service", "id": "treg-exa-contents", "version": "1"},
                "status": "retrieved",
                "observed_at": "2026-10-18T09:00:00Z",
                "excerpt": excerpt,
                "content_hash": sha256_digest(excerpt),
            }
        )

    mine = page("demo-security", "demo-security")
    verify_cited_evidence(
        customer_id="demo-security",
        account_id="acc-fernhollow",
        evidence_ids=[mine.evidence_id],
        evidence=[mine],
    )
    other = page("demo-security", "demo-elsewhere")
    with pytest.raises(CitationError, match="about another seller"):
        verify_cited_evidence(
            customer_id="demo-security",
            account_id="acc-fernhollow",
            evidence_ids=[other.evidence_id],
            evidence=[other],
        )
