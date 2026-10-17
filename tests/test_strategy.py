from __future__ import annotations

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from auggie.contracts import HeadcountRange, Mentions, Strategy

BASE: dict[str, Any] = {
    "strategy_id": "strategy-test-r1",
    "customer_id": "demo-test",
    "required": ["headcount"],
    "sources": {"careers": {"label": "careers page"}, "news": {"label": "news source"}},
    "criteria": [
        {
            "id": "headcount",
            "question": "Does the account have 50 to 500 staff?",
            "rule": "headcount_range",
            "source": "careers",
            "min": 50,
            "max": 500,
            "disqualifying": True,
        },
        {
            "id": "expansion",
            "question": "Has the account opened an office recently?",
            "rule": "mentions",
            "source": "news",
            "phrases": ["office"],
            "basis": "source_fact",
            "rationale": "The news source reports a new office.",
        },
    ],
    "plays": [
        {
            "id": "intro",
            "requires": ["expansion"],
            "template": "Hi {first_name}, congrats on the {city} office.\n",
            "fields": {"city": {"source": "news", "pattern": r"office in (\w+)"}},
        }
    ],
}


def strategy(**changes: Any) -> Strategy:
    data = copy.deepcopy(BASE)
    data.update(changes)
    return Strategy.model_validate(data)


def play(**changes: Any) -> list[dict[str, Any]]:
    data = copy.deepcopy(BASE["plays"][0])
    data.update(changes)
    return [data]


def test_a_valid_strategy_loads_each_rule_kind() -> None:
    loaded = strategy()
    assert isinstance(loaded.criterion("headcount"), HeadcountRange)
    assert isinstance(loaded.criterion("expansion"), Mentions)
    with pytest.raises(KeyError):
        loaded.criterion("missing")


def test_a_loaded_strategy_cannot_be_changed_in_place() -> None:
    loaded = strategy()
    with pytest.raises(TypeError):
        loaded.sources["rumor"] = loaded.sources["news"]  # type: ignore[index]
    with pytest.raises(TypeError):
        del loaded.plays[0].fields["city"]  # type: ignore[attr-defined]
    assert Strategy.model_validate_json(loaded.model_dump_json()) == loaded


def test_unknown_rule_kinds_are_rejected() -> None:
    criteria = copy.deepcopy(BASE["criteria"])
    criteria[1]["rule"] = "vibes"
    with pytest.raises(ValidationError, match="does not match any of the expected tags"):
        strategy(criteria=criteria)


def test_unknown_keys_are_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        strategy(owner="someone")


def test_criterion_ids_must_be_unique() -> None:
    criteria = copy.deepcopy(BASE["criteria"])
    criteria[1]["id"] = "headcount"
    with pytest.raises(ValidationError, match="criterion ids must be unique"):
        strategy(criteria=criteria)


def test_required_criteria_must_exist() -> None:
    with pytest.raises(ValidationError, match="required criterion 'missing'"):
        strategy(required=["missing"])


def test_criteria_must_name_a_known_source() -> None:
    criteria = copy.deepcopy(BASE["criteria"])
    criteria[0]["source"] = "rumor"
    with pytest.raises(ValidationError, match="unknown source 'rumor'"):
        strategy(criteria=criteria)


def test_headcount_range_must_be_ordered() -> None:
    criteria = copy.deepcopy(BASE["criteria"])
    criteria[0]["min"] = 600
    with pytest.raises(ValidationError, match="min cannot exceed max"):
        strategy(criteria=criteria)


def test_plays_must_require_defined_criteria() -> None:
    with pytest.raises(ValidationError, match="requires undefined criterion 'missing'"):
        strategy(plays=play(requires=["missing"]))


def test_template_placeholders_must_be_declared() -> None:
    with pytest.raises(ValidationError, match=r"undeclared fields: \['company'\]"):
        strategy(plays=play(template="Hi {first_name} at {company} in {city}.\n"))


def test_declared_fields_must_be_used() -> None:
    with pytest.raises(ValidationError, match=r"unused fields: \['city'\]"):
        strategy(plays=play(template="Hi {first_name}.\n"))


@pytest.mark.parametrize("placeholder", ["{city.upper}", "{city[0]}", "{city!r}", "{city:>9}"])
def test_placeholders_must_be_plain_names(placeholder: str) -> None:
    with pytest.raises(ValidationError, match="plain lowercase field name"):
        strategy(plays=play(template=f"Hi {{first_name}} in {placeholder}.\n"))


@pytest.mark.parametrize("pattern", [r"office in \w+", r"(office) in (\w+)", r"office in (\w+"])
def test_merge_field_patterns_need_one_capture_group(pattern: str) -> None:
    fields = {"city": {"source": "news", "pattern": pattern}}
    with pytest.raises(ValidationError, match=r"capture group|invalid pattern"):
        strategy(plays=play(fields=fields))


def test_merge_fields_must_name_a_known_source() -> None:
    fields = {"city": {"source": "rumor", "pattern": r"office in (\w+)"}}
    with pytest.raises(ValidationError, match="unknown source 'rumor'"):
        strategy(plays=play(fields=fields))
