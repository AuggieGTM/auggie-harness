"""Strategy loading, fixture acquisition and evidence validation over the Northstar inputs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from auggie.accounts import Account
from auggie.acquisition.fixture import FixtureAcquisition, RecordedResponse
from auggie.acquisition.validation import check_evidence
from auggie.contracts import (
    CustomerScope,
    ScopeViolation,
    SourceEvidence,
    SubjectRef,
    sha256_digest,
)
from auggie.strategy import StrategyError, load_strategy, load_strategy_at
from tests.conftest import NORTHSTAR, Scenario, variant

INPUTS = NORTHSTAR / "inputs"
COMMIT = "0000000000000000000000000000000000000001"


@pytest.fixture(scope="module")
def inputs() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((INPUTS / "inputs.json").read_text(encoding="utf-8"))
    return data


@pytest.fixture
def account(inputs: dict[str, Any]) -> Account:
    return Account.model_validate(inputs["account"])


@pytest.fixture
def subject(account: Account) -> SubjectRef:
    return SubjectRef(kind="account", id=account.account_id)


def acquire_all(inputs: dict[str, Any], account: Account) -> list[SourceEvidence]:
    adapter = FixtureAcquisition(RecordedResponse.model_validate(r) for r in inputs["responses"])
    return [
        adapter.acquire(
            evidence_id=f"ev-{account.key}-{source}",
            customer_id="demo-msp",
            subject=SubjectRef(kind="account", id=account.account_id),
            url=url,
        ).evidence
        for source, url in account.sources.items()
    ]


def test_inputs_scope_is_the_scenario_scope(inputs: dict[str, Any], scenario: Scenario) -> None:
    assert CustomerScope.model_validate(inputs["scope"]) == scenario.scope


def test_the_strategy_is_pinned_to_its_exact_bytes(scenario: Scenario) -> None:
    loaded = load_strategy(INPUTS, "strategy/msp.toml", commit=COMMIT, scope=scenario.scope)
    assert loaded.ref.path == "strategy/msp.toml"
    assert loaded.ref.commit == COMMIT
    assert loaded.ref.digest == sha256_digest((INPUTS / "strategy/msp.toml").read_bytes())
    assert loaded.strategy.strategy_id == "strategy-msp-icp-r1"
    assert [c.id for c in loaded.strategy.criteria] == [
        f.criterion for f in scenario.qualification.findings
    ]


def test_another_customers_strategy_is_refused(tmp_path: Path, scenario: Scenario) -> None:
    text = (INPUTS / "strategy/msp.toml").read_text(encoding="utf-8")
    (tmp_path / "other.toml").write_text(text.replace('"demo-msp"', '"demo-other"'))
    with pytest.raises(ScopeViolation, match="belongs to 'demo-other'"):
        load_strategy(tmp_path, "other.toml", commit=COMMIT, scope=scenario.scope)


@pytest.mark.parametrize("path", ["../msp.toml", "/etc/msp.toml", "strategy/../../msp.toml"])
def test_strategy_paths_stay_inside_the_knowledge_root(path: str, scenario: Scenario) -> None:
    with pytest.raises(StrategyError, match="inside the knowledge root"):
        load_strategy(INPUTS, path, commit=COMMIT, scope=scenario.scope)


def test_unparseable_strategy_files_are_reported(tmp_path: Path, scenario: Scenario) -> None:
    (tmp_path / "broken.toml").write_text("schema_version = \n")
    with pytest.raises(StrategyError, match=r"broken\.toml"):
        load_strategy(tmp_path, "broken.toml", commit=COMMIT, scope=scenario.scope)
    (tmp_path / "invalid.toml").write_text("schema_version = 2\n")
    with pytest.raises(ValidationError):
        load_strategy(tmp_path, "invalid.toml", commit=COMMIT, scope=scenario.scope)


def test_replayed_responses_become_the_scenario_evidence(
    inputs: dict[str, Any], account: Account, scenario: Scenario
) -> None:
    assert acquire_all(inputs, account) == list(scenario.evidence)


def test_a_url_with_no_recording_is_an_error(account: Account) -> None:
    with pytest.raises(LookupError, match="no recorded response"):
        FixtureAcquisition([]).acquire(
            evidence_id="ev-x",
            customer_id="demo-msp",
            subject=SubjectRef(kind="account", id=account.account_id),
            url=account.sources["careers"],
        )


def test_validation_admits_retrieved_evidence_and_rejects_the_failure(
    scenario: Scenario, subject: SubjectRef
) -> None:
    verdicts = [check_evidence(e, scope=scenario.scope, subject=subject) for e in scenario.evidence]
    assert [(v.evidence.evidence_id, v.rejected_for) for v in verdicts] == [
        ("ev-northstar-careers", None),
        ("ev-northstar-technology", None),
        ("ev-northstar-news", "retrieval_failed"),
    ]


def test_validation_rejects_evidence_about_another_subject(
    scenario: Scenario, subject: SubjectRef
) -> None:
    other = SourceEvidence.model_validate(
        variant(scenario.evidence[0], subject={"kind": "account", "id": "acc-elsewhere"})
    )
    assert check_evidence(other, scope=scenario.scope, subject=subject).rejected_for == (
        "wrong_subject"
    )


@pytest.mark.parametrize(
    "changes",
    # Documentation addresses (RFC 5737): not reserved names, and nobody's real site.
    [{"source_url": "https://203.0.113.5/careers"}, {"final_url": "https://198.51.100.7/jobs"}],
)
def test_synthetic_scopes_admit_only_reserved_domains(
    changes: dict[str, str], scenario: Scenario, subject: SubjectRef
) -> None:
    real = SourceEvidence.model_validate(variant(scenario.evidence[0], **changes))
    verdict = check_evidence(real, scope=scenario.scope, subject=subject)
    assert verdict.rejected_for == "non_reserved_domain"
    live = CustomerScope.model_validate(variant(scenario.scope, synthetic=False))
    assert check_evidence(real, scope=live, subject=subject).admitted


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def strategy_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "knowledge"
    (repo / "strategy").mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Synthetic Operator")
    git(repo, "config", "user.email", "operator@example.com")
    (repo / "strategy/msp.toml").write_bytes((INPUTS / "strategy/msp.toml").read_bytes())
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "Approve the MSP strategy")
    return repo


def test_a_strategy_is_read_as_committed_not_as_edited(tmp_path: Path, scenario: Scenario) -> None:
    repo = strategy_repo(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    (repo / "strategy/msp.toml").write_text("not even TOML", encoding="utf-8")
    loaded = load_strategy_at(repo, "strategy/msp.toml", revision="HEAD", scope=scenario.scope)
    assert loaded.ref.commit == head
    assert loaded.ref.digest == sha256_digest((INPUTS / "strategy/msp.toml").read_bytes())


@pytest.mark.parametrize("revision", ["no-such-branch", "--output=/tmp/x", "HEAD~5"])
def test_an_unknown_revision_is_refused(revision: str, tmp_path: Path, scenario: Scenario) -> None:
    repo = strategy_repo(tmp_path)
    with pytest.raises(StrategyError, match="cannot read"):
        load_strategy_at(repo, "strategy/msp.toml", revision=revision, scope=scenario.scope)


def test_a_path_missing_at_that_commit_is_refused(tmp_path: Path, scenario: Scenario) -> None:
    repo = strategy_repo(tmp_path)
    with pytest.raises(StrategyError, match="cannot read"):
        load_strategy_at(repo, "strategy/other.toml", revision="HEAD", scope=scenario.scope)
    with pytest.raises(StrategyError, match="inside the knowledge root"):
        load_strategy_at(repo, "../msp.toml", revision="HEAD", scope=scenario.scope)
