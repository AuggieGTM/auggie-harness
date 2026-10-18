"""The seller's strategy holds the signal vocabulary; the harness code holds none (ADR 0023)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from auggie.contracts import CustomerScope, FamilyOpenings, ListingPattern, StackMention, Strategy
from auggie.contracts.strategy import DEFAULT_LEVEL_WORDS, LADDER
from auggie.judgment.rules import assess
from auggie.strategy import load_strategy
from tests.conftest import EXAMPLES, Scenario, variant

ROOT = Path(__file__).resolve().parent.parent
SECURITY = EXAMPLES / "kestrel-security"


def security_strategy() -> Strategy:
    scope = CustomerScope.model_validate_json((SECURITY / "scope.json").read_text())
    loaded = load_strategy(
        SECURITY / "inputs", "strategy/security.toml", commit="0" * 40, scope=scope
    )
    return loaded.strategy


def strategies() -> list[Strategy]:
    """Every synthetic strategy, whatever its seller."""
    found = []
    for path in sorted(EXAMPLES.glob("*/inputs/strategy/*.toml")):
        example = path.parents[2]
        scope = CustomerScope.model_validate_json((example / "scope.json").read_text())
        relative = str(path.relative_to(example / "inputs"))
        found.append(load_strategy(example / "inputs", relative, commit="0" * 40, scope=scope))
    return [loaded.strategy for loaded in found]


def test_a_second_seller_in_another_domain_loads_its_own_vocabulary() -> None:
    strategy = security_strategy()
    hiring = strategy.signals.hiring
    assert hiring is not None and strategy.signals.stack is not None
    assert list(hiring.families) == ["detection", "secops", "identity"]  # file order is kept
    assert hiring.patterns.new_openings.label == "new detection openings"
    assert hiring.patterns.more_openings.min_increase == 3  # the default
    assert hiring.level_words("senior") == DEFAULT_LEVEL_WORDS["senior"]
    kinds = {type(c) for c in strategy.criteria}
    assert {FamilyOpenings, ListingPattern, StackMention} <= kinds


def test_the_harness_code_names_none_of_the_sellers_words() -> None:
    words: set[str] = set()
    for strategy in strategies():
        hiring, stack = strategy.signals.hiring, strategy.signals.stack
        for family in hiring.families.values() if hiring else ():
            words.update(family.titles)
        for term in stack.terms.values() if stack else ():
            words.update((*term.phrases, *term.near))
    assert words, "the synthetic sellers define no vocabulary to check"
    code = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src").rglob("*.py")).lower()
    found = sorted(
        w for w in words if re.search(rf"(?<![a-z0-9]){re.escape(w.lower())}(?![a-z0-9])", code)
    )
    assert found == []


def test_the_default_level_words_name_levels_only() -> None:
    assert tuple(DEFAULT_LEVEL_WORDS) == LADDER


def strategy_data(**changes: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "strategy_id": "s-1",
        "customer_id": "demo-security",
        "sources": {"security": {"label": "security page"}},
        "criteria": [
            {
                "id": "hiring",
                "question": "Hiring?",
                "rule": "hiring_family",
                "family": "detection",
            }
        ],
        "required": ["hiring"],
        "signals": {
            "hiring": {"families": {"detection": {"label": "d", "titles": ["detection engineer"]}}}
        },
    }
    data.update(changes)
    return data


@pytest.mark.parametrize(
    ("changes", "refused"),
    [
        ({"signals": {}}, "unknown hiring family"),
        (
            {
                "criteria": [
                    {
                        "id": "hiring",
                        "question": "Q?",
                        "rule": "hiring_family",
                        "family": "secops",
                    }
                ]
            },
            "unknown hiring family 'secops'",
        ),
        (
            {
                "criteria": [
                    {
                        "id": "hiring",
                        "question": "Q?",
                        "rule": "stack_term",
                        "source": "security",
                        "term": "none",
                    }
                ]
            },
            "unknown stack term",
        ),
        (
            {
                "signals": {
                    "hiring": {
                        "families": {"detection": {"label": "d", "titles": ["x"]}},
                        "patterns": {"baseline_days": 200, "max_lookback_days": 180},
                    }
                }
            },
            "baseline_days must be less than max_lookback_days",
        ),
        ({"signals": {"hiring": {"families": {}}}}, "at least one family"),
        (
            {
                "criteria": [
                    {
                        "id": "hiring",
                        "question": "Q?",
                        "rule": "hiring_pattern",
                        "family": "detection",
                        "pattern": "team_formed",
                    }
                ]
            },
            "pattern",
        ),
    ],
    ids=["no-vocabulary", "unknown-family", "unknown-term", "window", "empty", "unknown-pattern"],
)
def test_a_strategy_that_names_vocabulary_it_lacks_is_refused(
    changes: dict[str, Any], refused: str
) -> None:
    with pytest.raises(ValidationError, match=refused):
        Strategy.model_validate(strategy_data(**changes))


def test_a_level_may_get_the_sellers_own_words() -> None:
    data = strategy_data()
    data["signals"]["hiring"]["levels"] = {"senior": ["senior", "sr.", "experienced"]}
    hiring = Strategy.model_validate(data).signals.hiring
    assert hiring is not None
    assert hiring.level_words("senior") == ("senior", "sr.", "experienced")
    assert hiring.level_words("junior") == DEFAULT_LEVEL_WORDS["junior"]


def test_listing_and_stack_rules_stay_unknown_until_they_can_be_judged() -> None:
    from datetime import UTC, datetime

    strategy = security_strategy()
    findings = [
        assess(c, strategy, {}, datetime(2026, 10, 18, tzinfo=UTC)) for c in strategy.criteria
    ]
    assert {f.assessment for f in findings} == {"unknown"}


def test_a_stack_term_is_judged_from_the_stored_page_as_inference(scenario: Scenario) -> None:
    from datetime import UTC, datetime

    from auggie.acquisition.validation import Verdict
    from auggie.contracts import SourceEvidence, sha256_digest

    strategy = security_strategy()
    criterion = strategy.criterion("names-beaconlog")
    at = datetime(2026, 10, 18, tzinfo=UTC)

    def page(text: str) -> dict[str, Verdict]:
        changed = variant(scenario.evidence[1], excerpt=text, content_hash=sha256_digest(text))
        return {"security": Verdict(evidence=SourceEvidence.model_validate(changed))}

    met = assess(criterion, strategy, page("How we work\nOur SIEM is Beaconlog.\n"), at)
    assert (met.assessment, met.basis) == ("meets", "inference")
    assert met.evidence_ids == (scenario.evidence[1].evidence_id,)
    assert '"Our SIEM is Beaconlog."' in met.rationale and 'near "SIEM"' in met.rationale
    passing = assess(criterion, strategy, page("Customers like Beaconlog trust us."), at)
    assert passing.assessment == "unknown"


def test_a_stack_finding_fits_its_contract_for_the_longest_excerpt(scenario: Scenario) -> None:
    from datetime import UTC, datetime

    from auggie.acquisition.validation import Verdict
    from auggie.contracts import SourceEvidence, sha256_digest

    strategy = security_strategy()
    text = "SIEM" + "-" * 3987 + "Beaconlog"
    changed = variant(scenario.evidence[1], excerpt=text, content_hash=sha256_digest(text))
    verdict = Verdict(evidence=SourceEvidence.model_validate(changed))
    finding = assess(
        strategy.criterion("names-beaconlog"),
        strategy,
        {"security": verdict},
        datetime(2026, 10, 18, tzinfo=UTC),
    )
    assert finding.assessment == "meets" and len(finding.rationale) < 1000
