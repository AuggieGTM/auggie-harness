"""Careers snapshots are evidence of what a listing showed on one read (ADR 0024)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from auggie.contracts import CareersSnapshot, OpenRole, roles_digest


def role(n: int, title: str = "Detection Engineer") -> OpenRole:
    return OpenRole.model_validate(
        {"role_id": f"r-{n}", "title": title, "url": f"https://jobs.example/{n}"}
    )


def snapshot(**changes: Any) -> CareersSnapshot:
    roles = changes.pop("roles", (role(1), role(2)))
    data: dict[str, Any] = {
        "snapshot_id": "careers-kestrel-1",
        "customer_id": "demo-security",
        "account_id": "acc-kestrel",
        "listing": {
            "service": "greenhouse",
            "board": "kestrel",
            "url": "https://boards.greenhouse.example/kestrel",
        },
        "read_at": "2026-10-18T09:00:00Z",
        "status": "complete",
        "roles": [r.model_dump(mode="json") for r in roles],
        "content_hash": roles_digest(roles),
        "acquired_via": {"kind": "service", "id": "careers-reader", "version": "1"},
    }
    data.update(changes)
    return CareersSnapshot.model_validate(data)


def test_a_snapshot_keeps_its_roles_and_names_its_listing() -> None:
    taken = snapshot()
    assert [r.role_id for r in taken.roles] == ["r-1", "r-2"]
    assert taken.listing_key == "acc-kestrel/greenhouse:kestrel"


@pytest.mark.parametrize(
    ("changes", "refused"),
    [
        ({"status": "failed", "roles": ()}, "failed read has a reason"),
        (
            {"status": "failed", "failure_reason": "Timed out."},
            "failed read has a reason and no roles",
        ),
        ({"failure_reason": "Timed out."}, "only a failed read"),
        ({"roles": (role(1), role(1))}, "each role ID once"),
        ({"content_hash": "sha256:" + "0" * 64}, "content_hash does not match"),
    ],
    ids=["failed-no-reason", "failed-with-roles", "reason-on-success", "repeated-id", "hash"],
)
def test_a_snapshot_that_contradicts_itself_is_refused(
    changes: dict[str, Any], refused: str
) -> None:
    with pytest.raises(ValidationError, match=refused):
        snapshot(**changes)


def test_a_failed_read_is_kept_with_its_reason() -> None:
    failed = snapshot(status="failed", roles=(), failure_reason="The listing could not be read.")
    assert failed.roles == () and failed.failure_reason
