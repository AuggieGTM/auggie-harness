"""Rules qualification and template composition reproduce the Northstar records from inputs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from auggie.accounts import Account
from auggie.acquisition.validation import Verdict, check_evidence
from auggie.compose.template import ComposeBlocked, compose_draft, select_play
from auggie.contracts import QualificationDecision, SourceEvidence, SubjectRef, sha256_digest
from auggie.judgment.rules import RulesQualifier
from auggie.strategy import LoadedStrategy, load_strategy
from tests.conftest import NORTHSTAR, Scenario, variant

INPUTS = NORTHSTAR / "inputs"
QUALIFIED_AT = datetime(2026, 10, 14, 16, 5, tzinfo=UTC)
COMPOSED_AT = datetime(2026, 10, 14, 16, 10, tzinfo=UTC)


@pytest.fixture(scope="module")
def account() -> Account:
    inputs = json.loads((INPUTS / "inputs.json").read_text(encoding="utf-8"))
    return Account.model_validate(inputs["account"])


@pytest.fixture
def strategy(scenario: Scenario) -> LoadedStrategy:
    return load_strategy(
        INPUTS,
        "strategy/msp.toml",
        commit=scenario.qualification.strategy.commit,
        scope=scenario.scope,
    )


def verdicts(scenario: Scenario, **excerpts: str | None) -> dict[str, Verdict]:
    """Validation verdicts per source kind, with optional replacement excerpts.

    ``None`` turns a source into a failed retrieval.
    """
    subject = SubjectRef(kind="account", id="acc-northstar")
    result = {}
    for kind, evidence in zip(("careers", "technology", "news"), scenario.evidence, strict=True):
        if kind in excerpts:
            excerpt = excerpts[kind]
            changes: dict[str, Any] = {"excerpt": excerpt, "failure_reason": None}
            if excerpt is None:
                changes |= {
                    "status": "failed",
                    "content_hash": None,
                    "failure_reason": "Timed out.",
                }
            else:
                changes |= {"status": "retrieved", "content_hash": sha256_digest(excerpt)}
            evidence = SourceEvidence.model_validate(variant(evidence, **changes))
        result[kind] = check_evidence(evidence, scope=scenario.scope, subject=subject)
    return result


def qualify(
    strategy: LoadedStrategy, sources: dict[str, Verdict], at: datetime = QUALIFIED_AT
) -> QualificationDecision:
    return RulesQualifier().qualify(
        decision_id="qual-northstar-001",
        customer_id="demo-msp",
        account_id="acc-northstar",
        strategy=strategy,
        sources=sources,
        at=at,
    )


def assessments(decision: QualificationDecision) -> dict[str, str]:
    return {f.criterion: f.assessment for f in decision.findings}


def test_rules_reproduce_the_scenario_decision(
    scenario: Scenario, strategy: LoadedStrategy
) -> None:
    assert qualify(strategy, verdicts(scenario)) == scenario.qualification


def test_a_failed_source_leaves_its_criteria_unknown_never_unmet(
    scenario: Scenario, strategy: LoadedStrategy
) -> None:
    decision = qualify(strategy, verdicts(scenario, careers=None))
    assert assessments(decision)["headcount-50-to-500"] == "unknown"
    assert assessments(decision)["active-it-hiring"] == "unknown"
    assert decision.outcome == "needs_review"
    assert "ev-northstar-careers" not in decision.cited_evidence_ids
    assert "Does the account have 50 to 500 staff?" in decision.unknowns


def test_every_source_failing_needs_review_and_cites_nothing(
    scenario: Scenario, strategy: LoadedStrategy
) -> None:
    decision = qualify(strategy, verdicts(scenario, careers=None, technology=None))
    assert set(assessments(decision).values()) == {"unknown"}
    assert decision.outcome == "needs_review"
    assert decision.cited_evidence_ids == frozenset()


def test_an_evidenced_headcount_outside_the_range_disqualifies(
    scenario: Scenario, strategy: LoadedStrategy
) -> None:
    small = "Tiny Co is hiring an IT Technician to run the help desk for our 12 staff."
    decision = qualify(strategy, verdicts(scenario, careers=small))
    assert decision.outcome == "disqualified"
    assert decision.disqualifiers == ("headcount-50-to-500",)


def test_a_stale_posting_does_not_count_as_active_hiring(
    scenario: Scenario, strategy: LoadedStrategy
) -> None:
    decision = qualify(strategy, verdicts(scenario), at=datetime(2026, 12, 1, tzinfo=UTC))
    assert assessments(decision)["active-it-hiring"] == "unknown"
    assert decision.outcome == "needs_review"


def test_the_play_template_reproduces_the_scenario_draft(
    scenario: Scenario, strategy: LoadedStrategy, account: Account
) -> None:
    sources = verdicts(scenario)
    decision = qualify(strategy, sources)
    play = select_play(strategy, decision)
    assert play is not None
    draft = compose_draft(
        artifact_id="draft-northstar-001",
        play=play,
        strategy=strategy,
        decision=decision,
        account=account,
        sources=sources,
        at=COMPOSED_AT,
    )
    assert draft.content.encode("utf-8") == (NORTHSTAR / scenario.draft.file).read_bytes()
    assert draft.ref == scenario.draft.artifact
    assert draft.recipient_id == scenario.draft.recipient_id
    assert draft.evidence_ids == ("ev-northstar-careers", "ev-northstar-technology")
    assert draft.strategy == scenario.qualification.strategy


def test_no_play_applies_without_its_criteria(
    scenario: Scenario, strategy: LoadedStrategy, account: Account
) -> None:
    sources = verdicts(scenario, technology=None)
    decision = qualify(strategy, sources)
    assert decision.outcome == "qualified"
    assert select_play(strategy, decision) is None
    with pytest.raises(ComposeBlocked, match="does not apply"):
        compose_draft(
            artifact_id="draft-x",
            play=strategy.strategy.plays[0],
            strategy=strategy,
            decision=decision,
            account=account,
            sources=sources,
            at=COMPOSED_AT,
        )


def test_a_field_missing_from_the_evidence_blocks_the_draft(
    scenario: Scenario, strategy: LoadedStrategy, account: Account
) -> None:
    other_platform = "We run on a mix of tools and plan to consolidate device-management this year."
    sources = verdicts(scenario, technology=other_platform)
    decision = qualify(strategy, sources)
    play = select_play(strategy, decision)
    assert play is not None
    with pytest.raises(ComposeBlocked, match="'platform' is not found in the technology page"):
        compose_draft(
            artifact_id="draft-x",
            play=play,
            strategy=strategy,
            decision=decision,
            account=account,
            sources=sources,
            at=COMPOSED_AT,
        )
