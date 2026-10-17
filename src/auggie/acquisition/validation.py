"""Validate stage: admit evidence that may support findings and reject the rest, with a reason."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from auggie.contracts import Actor, CustomerScope, SourceEvidence, SubjectRef

RejectionReason = Literal["retrieval_failed", "wrong_subject", "non_reserved_domain"]

VALIDATOR = Actor(kind="rule", id="evidence-checks", version="0")

RESERVED_HOST = re.compile(
    r"(^|\.)(example|test|invalid|localhost)$|^example\.(com|net|org)$", re.IGNORECASE
)


@dataclass(frozen=True)
class Verdict:
    evidence: SourceEvidence
    rejected_for: RejectionReason | None = None

    @property
    def admitted(self) -> bool:
        return self.rejected_for is None


def check_evidence(
    evidence: SourceEvidence, *, scope: CustomerScope, subject: SubjectRef
) -> Verdict:
    """Admit ``evidence`` only if it was retrieved, is about ``subject``, and fits the scope.

    A synthetic scope admits only reserved domains, so a synthetic run can never
    be built on a real company's pages.
    """
    if not evidence.usable:
        return Verdict(evidence, "retrieval_failed")
    if evidence.subject != subject:
        return Verdict(evidence, "wrong_subject")
    urls = [evidence.source_url, *([evidence.final_url] if evidence.final_url else [])]
    if scope.synthetic and not all(RESERVED_HOST.search(url.host or "") for url in urls):
        return Verdict(evidence, "non_reserved_domain")
    return Verdict(evidence)
