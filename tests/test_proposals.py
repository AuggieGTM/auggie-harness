"""Strategy proposals from outcomes (ADR 0028): plays and proofs in order of their results,
as a patch the person applies. Synthetic sellers only; nothing is applied by the harness."""

from __future__ import annotations

import os
import subprocess
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

from auggie.acquisition.fixture import FixtureAcquisition, RecordedListings
from auggie.cli import main
from auggie.learning import Tally
from auggie.operate import deliver_draft, report_outcome, research_prospect, review_draft
from auggie.pipeline import read_proof_pages
from auggie.proposals import MIN_SETTLED, patch, propose, ranked
from auggie.store import Store
from auggie.strategy import LoadedStrategy, load_strategy
from auggie.synthetic import load_inputs
from tests.conftest import EXAMPLES, MONGODB_URI, mongo_backend

KESTREL = EXAMPLES / "kestrel-security"
INPUTS = load_inputs(KESTREL)
SCOPE = INPUTS.scope
STRATEGY_PATH = "examples/synthetic/kestrel-security/inputs/strategy/security.toml"
TEXT = (KESTREL / "inputs" / "strategy" / "security.toml").read_text(encoding="utf-8")


def tally(key: str, positive: int, negative: int) -> Tally:
    return Tally(key, delivered=positive + negative, positive=positive, negative=negative)


def loaded_from(text: str, tmp_path: Path) -> LoadedStrategy:
    (tmp_path / "strategy").mkdir(exist_ok=True)
    (tmp_path / "strategy" / "s.toml").write_text(text, encoding="utf-8")
    return load_strategy(tmp_path, "strategy/s.toml", commit="0" * 40, scope=SCOPE)


def test_only_items_with_enough_results_move_and_ties_keep_their_order() -> None:
    results = {
        "a": tally("a", 2, 8),
        "b": tally("b", 6, 4),
        "c": tally("c", 9, 0),
        "d": tally("d", 6, 4),
    }
    # c has too few settled deliveries, so it keeps its place.
    assert ranked(("a", "b", "c", "d"), results, MIN_SETTLED) == ("b", "d", "c", "a")
    assert ranked(("a", "c"), results, MIN_SETTLED) == ("a", "c")
    assert ranked(("b", "d"), results, MIN_SETTLED) == ("b", "d")  # a tie keeps the order


def test_plays_are_reordered_in_the_strategys_own_text(tmp_path: Path) -> None:
    loaded = loaded_from(TEXT, tmp_path)
    plays = {
        "detection-triage": tally("detection-triage", 3, 9),
        "detection-build-out": tally("detection-build-out", 6, 4),
    }
    made, by_hand, text = propose(loaded, plays, {})
    assert by_hand == []
    (proposal,) = made
    assert proposal.after == ("detection-build-out", "detection-triage")
    assert proposal.reasons == (
        "detection-build-out: 6 of 10 settled were positive",
        "detection-triage: 3 of 12 settled were positive",
    )
    # The comment above the fallback play moves with it, and nothing else changes.
    assert text.index("# Used when no proof") < text.index('id = "detection-build-out"')
    assert text.index('id = "detection-build-out"') < text.index('id = "detection-triage"')
    before, after = tomllib.loads(TEXT), tomllib.loads(text)
    assert [p["id"] for p in after["plays"]] == ["detection-build-out", "detection-triage"]
    assert after["plays"] == list(reversed(before["plays"]))
    after["plays"] = before["plays"]
    assert after == before


def test_the_patch_applies_in_the_strategy_repository(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "strategy").mkdir(parents=True)
    (repo / "strategy" / "s.toml").write_text(TEXT, encoding="utf-8")
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "strategy"], check=True)
    loaded = load_strategy(repo, "strategy/s.toml", commit="0" * 40, scope=SCOPE)
    plays = {
        "detection-triage": tally("detection-triage", 3, 9),
        "detection-build-out": tally("detection-build-out", 6, 4),
    }
    _, _, text = propose(loaded, plays, {})
    diff = tmp_path / "proposal.patch"
    diff.write_text(patch(loaded, text), encoding="utf-8")
    subprocess.run([*git, "apply", "--check", str(diff)], check=True)
    subprocess.run([*git, "apply", str(diff)], check=True)
    assert (repo / "strategy" / "s.toml").read_text(encoding="utf-8") == text


def two_proofs(proofs_line: str) -> str:
    second = TEXT.replace(
        "[pains.alert-triage]",
        "[proofs.saltmarsh-scale]\n"
        'customer = "Saltmarsh"\n'
        'url = "https://lanternwatch.example/customers/saltmarsh"\n'
        'claim = "Saltmarsh triages ten thousand alerts a day."\n'
        'kind = "scale"\n\n'
        "[pains.alert-triage]",
    )
    return second.replace('proofs = ["harborview-triage"]', proofs_line)


def test_a_pains_proofs_are_reordered_on_their_own_line(tmp_path: Path) -> None:
    loaded = loaded_from(
        two_proofs('proofs = ["harborview-triage", "saltmarsh-scale"]  # best first'), tmp_path
    )
    proofs = {
        "harborview-triage": tally("harborview-triage", 1, 9),
        "saltmarsh-scale": tally("saltmarsh-scale", 5, 5),
    }
    (proposal,), by_hand, text = propose(loaded, {}, proofs)
    assert proposal.what == "pain alert-triage's proofs" and by_hand == []
    assert 'proofs = ["saltmarsh-scale", "harborview-triage"]  # best first\n' in text


def test_a_layout_the_harness_cannot_rewrite_safely_is_left_to_the_person(tmp_path: Path) -> None:
    spread = two_proofs('proofs = [\n  "harborview-triage",\n  "saltmarsh-scale",\n]')
    proofs = {
        "harborview-triage": tally("harborview-triage", 1, 9),
        "saltmarsh-scale": tally("saltmarsh-scale", 5, 5),
    }
    made, by_hand, text = propose(loaded_from(spread, tmp_path), {}, proofs)
    assert made == [] and text == spread
    assert by_hand == ["list pain alert-triage's proofs as: saltmarsh-scale, harborview-triage"]
    # Plays with another table between them are not one run of blocks either.
    split = TEXT.replace(
        "# Used when no proof", '[sources.extra]\nlabel = "extra page"\n\n# Used when no proof'
    )
    plays = {
        "detection-triage": tally("detection-triage", 3, 9),
        "detection-build-out": tally("detection-build-out", 6, 4),
    }
    made, by_hand, _ = propose(loaded_from(split, tmp_path), plays, {})
    assert made == [] and by_hand == [
        "put the plays in this order: detection-build-out, detection-triage"
    ]


def test_a_proposal_needs_ten_settled_deliveries_and_the_strategys_text(tmp_path: Path) -> None:
    loaded = loaded_from(TEXT, tmp_path)
    with pytest.raises(ValueError, match="at least 10 settled deliveries"):
        propose(loaded, {}, {}, min_settled=5)
    with pytest.raises(ValueError, match="text is not at hand"):
        propose(LoadedStrategy(loaded.strategy, loaded.ref), {}, {})


# From outcomes to a patch, through the command


def deliver_one(store: Store, rules: LoadedStrategy, outcome: str) -> str:
    result = research_prospect(
        store,
        strategy=rules,
        prospect=INPUTS.account,
        acquisition=FixtureAcquisition(INPUTS.responses),
        operator="demo-seller",
        listings=RecordedListings([INPUTS.listings[-1]]),
    )
    assert result.draft is not None
    draft_id = result.draft.ref.artifact_id
    answers: Iterator[str] = iter(["y", "", "y"])
    review_draft(
        store,
        draft_id=draft_id,
        reviewer="demo-reviewer",
        ask=lambda _: next(answers),
        out=lambda _: None,
    )
    deliver_draft(store, draft_id=draft_id)
    report_outcome(store, draft_id=draft_id, outcome=outcome, reporter="demo-reviewer")  # type: ignore[arg-type]
    return draft_id


def test_learn_proposes_a_patch_from_reported_outcomes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from auggie.store.mongodb import MongoBackend

    rules = load_strategy(KESTREL / "inputs", INPUTS.strategy.path, commit="0" * 40, scope=SCOPE)
    repo = Path(__file__).resolve().parent.parent
    with mongo_backend() as backend:
        assert isinstance(backend, MongoBackend)
        store = backend.open(SCOPE)
        # Ten without a usable proof go out from the fallback play; six get replies.
        for n in range(10):
            deliver_one(store, rules, "positive_reply" if n < 6 else "no_reply")
        read_proof_pages(
            store,
            strategy=rules,
            acquisition=FixtureAcquisition(INPUTS.responses),
            run_id=None,
            now=lambda: INPUTS.responses[-1].observed_at,
        )
        # Ten more quote the proof; three get replies.
        for n in range(10):
            deliver_one(store, rules, "meeting_booked" if n < 3 else "negative_reply")
        diff = tmp_path / "proposal.patch"
        store_args = [
            "--mongodb",
            os.environ[MONGODB_URI],
            "--database-prefix",
            backend.database_prefix,
        ]
        argv = [
            "learn",
            str(KESTREL / "scope.json"),
            "--strategy-repo",
            str(repo),
            "--strategy-path",
            STRATEGY_PATH,
        ]
        assert main([*argv, "--propose", str(diff), "--json", *store_args]) == 1
        assert "JSON commands only read" in capsys.readouterr().out
        assert main([*argv, "--propose", str(diff), *store_args]) == 0
        out = capsys.readouterr().out
    assert "20 deliveries." in out
    assert "proposed  plays: detection-build-out, detection-triage" in out
    assert "detection-build-out: 6 of 10 settled were positive" in out
    assert stat_mode(diff) == 0o600
    body = diff.read_text(encoding="utf-8")
    assert body.startswith(f"--- a/{STRATEGY_PATH}\n+++ b/{STRATEGY_PATH}\n")
    with mongo_backend() as again:
        assert (
            main(
                [
                    *argv,
                    "--propose",
                    str(diff),
                    "--mongodb",
                    os.environ[MONGODB_URI],
                    "--database-prefix",
                    again.database_prefix,
                ]
            )
            == 1
        )
    assert "exists; give a new file" in capsys.readouterr().out


def stat_mode(path: Path) -> int:
    import stat

    return stat.S_IMODE(path.stat().st_mode)
