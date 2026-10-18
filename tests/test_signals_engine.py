"""The signal engine: matching the seller's words, and ADR 0024's table, case by case."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from auggie.contracts.strategy import Hiring, Technology
from auggie.signals import family_of, level_of, mentions, stack_matches
from auggie.signals.tally import (
    Reading,
    ReadStatus,
    Role,
    choose_baseline,
    count_once,
    family_openings,
    listing_pattern,
)

NOW = datetime(2026, 10, 18, tzinfo=UTC)
LISTING = "acc-kestrel/greenhouse:kestrel"
HIRING = Hiring.model_validate(
    {
        "families": {
            "detection": {
                "label": "detection",
                "titles": ["detection engineer", "threat detection"],
            },
            "secops": {"label": "secops", "titles": ["soc analyst", "security operations"]},
        }
    }
)


def role(title: str, n: int = 0, *, days_ago: int | None = None) -> Role:
    posted = NOW - timedelta(days=days_ago) if days_ago is not None else None
    return Role(f"r-{n}-{title}", title, f"https://jobs.example/{n}", posted)


def reading(
    *titles: str,
    status: ReadStatus = "complete",
    days_ago: int = 0,
    order: int = 0,
    dated: int | None = None,
) -> Reading:
    roles = tuple(role(t, i, days_ago=dated) for i, t in enumerate(titles))
    return Reading(
        f"snap-{days_ago}-{order}", LISTING, NOW - timedelta(days=days_ago), status, roles, order
    )


DE = "Detection Engineer"


# Matching


@pytest.mark.parametrize(
    ("text", "phrase", "found"),
    [
        ("Senior C++ Engineer", "C++", True),
        ("C++17 developer", "C++", False),
        ("ASP.NET developer", ".NET", False),
        ("a .NET shop", ".NET", True),
        ("Detection\t  Engineer", "detection engineer", True),
        ("Detection Engineering", "detection engineer", False),
        ("go-to-market lead", "go-to-market", True),
    ],
)
def test_phrases_match_as_written(text: str, phrase: str, found: bool) -> None:
    assert mentions(text, phrase) is found


def test_a_role_belongs_to_the_first_family_its_title_matches() -> None:
    assert family_of("Threat Detection, Security Operations Lead", HIRING) == "detection"
    assert family_of("SOC Analyst II", HIRING) == "secops"
    assert family_of("Account Executive", HIRING) is None


@pytest.mark.parametrize(
    ("title", "level"),
    [
        ("Senior Product Manager", "manager"),
        ("Staff Detection Engineer", "staff"),
        ("Detection Engineer", None),
        ("Head of Detection", "director"),
        ("VP, Security", "vp"),
        ("Jr SOC Analyst", "junior"),
        ("Sr. Detection Engineer", "senior"),
    ],
)
def test_each_title_gets_the_highest_level_it_names(title: str, level: str | None) -> None:
    assert level_of(title, HIRING) == level


BEACON = Technology(label="Beaconlog", phrases=("Beaconlog",), near=("SIEM",), near_words=8)


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Our SIEM is Beaconlog.", True),
        ("Beaconlog one two three four five six seven eight SIEM", True),
        ("Beaconlog one two three four five six seven eight nine SIEM", False),
        ("SIEM: a/b/c/d/e/f/g/h/i Beaconlog", False),
        ("SIEM: a/b/c/d/e/f/g/h Beaconlog", True),
        ("Our SIEM is\nBeaconlog", False),
        ("Customers using Beaconlog", False),
    ],
    ids=[
        "near",
        "eight-between",
        "nine-between",
        "punctuation-nine",
        "punctuation-eight",
        "next-line",
        "no-near",
    ],
)
def test_a_stack_term_counts_only_near_its_words_on_one_line(text: str, found: bool) -> None:
    assert bool(stack_matches(text, BEACON)) is found


def test_a_term_without_near_words_counts_anywhere() -> None:
    term = Technology(label="Beaconlog", phrases=("Beaconlog",))
    (match,) = stack_matches("intro\n  Customers using Beaconlog  \nend", term)
    assert "Customers using Beaconlog" in match.quote and match.near is None


# hiring_family


@pytest.mark.parametrize(
    ("current", "answer"),
    [
        (reading(DE, DE), "met"),
        (reading(DE), "unmet"),
        (reading(DE, DE, status="partial"), "met"),
        (reading(DE, status="partial"), "unknown"),
        (reading(DE, DE, status="inconsistent"), "unknown"),
        (reading(status="failed"), "unknown"),
    ],
    ids=[
        "complete-met",
        "complete-unmet",
        "partial-met",
        "partial-short",
        "inconsistent",
        "failed",
    ],
)
def test_family_openings(current: Reading, answer: str) -> None:
    assert family_openings(current, "detection", 2, HIRING).answer == answer


# new_openings and more_openings


def baseline(*titles: str, status: ReadStatus = "complete", days_ago: int = 30) -> Reading:
    return reading(*titles, status=status, days_ago=days_ago)


@pytest.mark.parametrize(
    ("current", "before", "answer"),
    [
        (reading(DE, DE), [baseline()], "met"),
        (reading(DE, DE, status="partial"), [baseline()], "met"),
        (reading(DE), [baseline()], "unmet"),
        (reading(DE, status="partial"), [baseline()], "unknown"),
        (reading(DE, status="partial"), [baseline(DE)], "unmet"),
        (reading(DE, DE), [], "unknown"),
        (reading(DE, DE), [baseline(status="partial")], "unknown"),
        (reading(DE, DE, status="failed"), [baseline()], "unknown"),
    ],
    ids=[
        "met",
        "partial-met",
        "complete-unmet",
        "partial-short",
        "had-openings",
        "no-baseline",
        "partial-baseline",
        "failed",
    ],
)
def test_new_openings(current: Reading, before: list[Reading], answer: str) -> None:
    assert listing_pattern("new_openings", current, before, "detection", HIRING).answer == answer


def test_a_capped_earlier_reading_cannot_fake_new_openings() -> None:
    # Pi's case: a partial earlier reading shows none, but unread roles may have been there.
    outcome = listing_pattern(
        "new_openings", reading(DE, DE), [baseline(status="partial")], "detection", HIRING
    )
    assert outcome.answer == "unknown"


@pytest.mark.parametrize(
    ("current", "answer"),
    [
        (reading(DE, DE, DE, DE), "met"),
        (reading(DE, DE, DE, DE, status="partial"), "met"),
        (reading(DE, DE), "unmet"),
        (reading(DE, DE, status="partial"), "unknown"),
    ],
)
def test_more_openings(current: Reading, answer: str) -> None:
    before = [baseline(DE)]
    assert listing_pattern("more_openings", current, before, "detection", HIRING).answer == answer


def test_the_baseline_is_the_newest_complete_reading_in_the_window() -> None:
    current = reading(DE)
    too_new, older, oldest = baseline(days_ago=1), baseline(days_ago=30), baseline(days_ago=60)
    too_old, partial = baseline(days_ago=200), baseline(days_ago=29, status="partial")
    chosen = choose_baseline(current, [too_new, oldest, older, too_old, partial], HIRING)
    assert chosen == older
    first, second = reading(days_ago=30, order=1), reading(days_ago=30, order=2)
    assert choose_baseline(current, [second, first], HIRING) == second


# senior_openings and leader_opening


SDE, JDE, MDE = (
    "Senior Detection Engineer",
    "Junior Detection Engineer",
    "Detection Engineering Manager",
)


@pytest.mark.parametrize(
    ("current", "answer"),
    [
        (reading(SDE, SDE, SDE), "met"),
        (reading(SDE, SDE, SDE, DE), "unknown"),
        (reading(SDE, SDE, SDE, JDE), "unmet"),
        (reading(SDE, DE), "unmet"),
        (reading(SDE, SDE, DE), "unknown"),
        (reading(SDE, SDE, SDE, status="partial"), "unknown"),
    ],
    ids=[
        "all-senior",
        "one-unlevelled",
        "junior",
        "too-few-even-if-unlevelled-senior",
        "could-be",
        "partial",
    ],
)
def test_senior_openings(current: Reading, answer: str) -> None:
    assert listing_pattern("senior_openings", current, [], "detection", HIRING).answer == answer


@pytest.mark.parametrize(
    ("current", "answer"),
    [
        (reading("Threat Detection Engineering Manager", SDE), "met"),
        (reading("Threat Detection Engineering Manager", SDE, SDE), "unmet"),
        (reading(DE), "unknown"),
        (reading(SDE), "unmet"),
        (reading(), "unmet"),
        (reading("Threat Detection Engineering Manager", status="partial"), "unknown"),
    ],
    ids=["leader-small", "too-large", "unlevelled", "no-leader", "empty", "partial"],
)
def test_leader_opening(current: Reading, answer: str) -> None:
    assert listing_pattern("leader_opening", current, [], "detection", HIRING).answer == answer


# long_listed


@pytest.mark.parametrize(
    ("current", "answer"),
    [
        (reading(DE, dated=130), "met"),
        (reading(DE, dated=130, status="partial"), "met"),
        (reading(DE, dated=10), "unmet"),
        (reading(DE), "unknown"),
        (reading(), "unmet"),
        (reading(DE, dated=10, status="partial"), "unknown"),
    ],
    ids=["old", "old-partial", "young", "undated", "empty", "young-partial"],
)
def test_long_listed(current: Reading, answer: str) -> None:
    assert listing_pattern("long_listed", current, [], "detection", HIRING).answer == answer


def test_ages_are_measured_from_the_reading_not_today() -> None:
    old_reading = Reading(
        "snap-old", LISTING, NOW - timedelta(days=400), "complete", (role(DE, days_ago=430),)
    )
    assert listing_pattern("long_listed", old_reading, [], "detection", HIRING).answer == "unmet"


def test_an_inconsistent_or_failed_reading_settles_no_pattern() -> None:
    for status in ("inconsistent", "failed"):
        current = reading(SDE, SDE, SDE, status=status)
        for pattern in (
            "new_openings",
            "more_openings",
            "senior_openings",
            "leader_opening",
            "long_listed",
        ):
            outcome = listing_pattern(pattern, current, [baseline()], "detection", HIRING)
            assert outcome.answer == "unknown", (status, pattern)


def test_an_outcome_cites_its_readings_and_at_most_three_roles() -> None:
    current = reading(DE, DE, DE, DE, DE)
    outcome = listing_pattern("new_openings", current, [baseline()], "detection", HIRING)
    assert outcome.readings == ("snap-0-0", "snap-30-0") and len(outcome.roles) == 3


# Edges found in review


@pytest.mark.parametrize(
    "vocabulary",
    [
        {"families": {"x": {"label": "x", "titles": ["  "]}}},
        {"families": {"x": {"label": "x", "titles": ["x"]}}, "levels": {"vp": [" "]}},
    ],
    ids=["family-title", "level-word"],
)
def test_a_blank_vocabulary_phrase_is_refused(vocabulary: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="blank"):
        Hiring.model_validate(vocabulary)


@pytest.mark.parametrize("field", ["phrases", "near"])
def test_a_blank_stack_phrase_is_refused(field: str) -> None:
    data: dict[str, object] = {"label": "B", "phrases": ["Beaconlog"], field: [" "]}
    with pytest.raises(ValidationError, match="blank"):
        Technology.model_validate(data)


def test_a_reading_counts_each_role_once() -> None:
    once = role(DE, 1)
    with pytest.raises(ValueError, match="role ID twice"):
        Reading("snap", LISTING, NOW, "complete", (once, once))
    assert count_once([once, once]) == ((once,), True)
    retitled = Role(once.role_id, "Senior Detection Engineer", once.url)
    assert count_once([once, retitled]) == ((once,), False)


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("éBeaconlog", False),
        ("Beaconlogé", False),
        ("Beaconlog é", True),
        ("Beaconlog٣", False),
        ("foo_Beaconlog", True),
    ],
    ids=["letter-before", "letter-after", "spaced", "arabic-digit", "underscore"],
)
def test_boundaries_hold_for_letters_and_digits_in_any_script(text: str, found: bool) -> None:
    assert mentions(text, "Beaconlog") is found


def test_words_in_any_script_count_towards_the_near_window() -> None:
    eight, nine = " ".join(["é"] * 8), " ".join(["é"] * 9)
    assert stack_matches(f"SIEM {eight} Beaconlog", BEACON)
    assert not stack_matches(f"SIEM {nine} Beaconlog", BEACON)


def test_a_plain_phrase_may_span_lines_but_one_with_near_words_may_not() -> None:
    plain = Technology(label="Beacon Log", phrases=("Beacon Log",))
    near = Technology(label="Beacon Log", phrases=("Beacon Log",), near=("SIEM",))
    assert stack_matches("Our SIEM is Beacon\nLog", plain)
    assert not stack_matches("Our SIEM is Beacon\nLog", near)


def test_a_quote_always_holds_the_match() -> None:
    line = "An introduction that goes on and on. " * 12 + "Our SIEM is Beaconlog."
    (match,) = stack_matches(line, BEACON)
    assert "Our SIEM is Beaconlog." in match.quote and len(match.quote) <= 320


def test_a_partial_reading_is_never_given_as_an_exact_count() -> None:
    current = reading(DE, DE, DE, DE, status="partial")
    new = listing_pattern("new_openings", current, [baseline()], "detection", HIRING)
    more = listing_pattern("more_openings", current, [baseline(DE)], "detection", HIRING)
    assert "at least 4 open now" in new.reason and "at least 4 now" in more.reason


def test_an_unknown_answer_cites_every_reading_it_speaks_of() -> None:
    outcome = listing_pattern(
        "new_openings", reading(DE, status="partial"), [baseline()], "detection", HIRING
    )
    assert outcome.answer == "unknown" and outcome.readings == ("snap-0-0", "snap-30-0")


def test_a_baseline_is_only_ever_from_the_same_listing() -> None:
    elsewhere = Reading("snap-other", "acc-other/lever:other", NOW - timedelta(days=30), "complete")
    assert choose_baseline(reading(DE), [elsewhere], HIRING) is None


def test_answers_follow_the_vocabulary_not_the_words() -> None:
    """The same listing shape gives the same answers under another seller's renamed words."""
    other = Hiring.model_validate(
        {
            "families": {
                "lifecycle": {"label": "lifecycle", "titles": ["lifecycle marketer"]},
                "analytics": {"label": "analytics", "titles": ["analytics engineer"]},
            }
        }
    )

    def answers(vocabulary: Hiring, family: str, title: str) -> list[str]:
        current = reading(*[title] * 3, f"Senior {title}")
        return [
            family_openings(current, family, 2, vocabulary).answer,
            *(
                listing_pattern(p, current, [baseline()], family, vocabulary).answer
                for p in ("new_openings", "more_openings", "senior_openings", "leader_opening")
            ),
        ]

    assert answers(HIRING, "detection", DE) == answers(other, "lifecycle", "Lifecycle Marketer")
    swapped = Hiring.model_validate(
        {
            "families": {
                "b": {"label": "b", "titles": ["engineer"]},
                "a": {"label": "a", "titles": [DE]},
            }
        }
    )
    assert family_of(DE, swapped) == "b"  # the strategy's order decides, not the words
