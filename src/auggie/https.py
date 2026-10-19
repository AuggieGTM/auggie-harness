"""HTTPS for provider adapters: fixed origins, no redirects, and errors that never quote a request.

Each adapter builds a ``LimitedTransport`` for the origins it may reach. A
redirect would carry its credential to another address, so none is followed.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_BODY_BYTES = 2_000_000


@dataclass(frozen=True)
class HttpRequest:
    method: str
    url: str
    headers: Mapping[str, str] = field(repr=False)
    body: bytes = field(repr=False)
    timeout: float

    def __repr__(self) -> str:
        names = ", ".join(sorted(self.headers))
        return f"HttpRequest({self.method} {self.url}, headers: {names})"


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    """Header names in lower case."""
    body: bytes


class Transport(Protocol):
    def send(self, request: HttpRequest) -> HttpResponse:
        """Send ``request``. Raises ``OSError`` (including timeouts) if no response arrives."""
        ...


class NoRedirects(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: it would carry the credential to another address."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> urllib.request.Request | None:
        return None


class LimitedTransport:
    """Sends requests with the standard library, only to ``origins``, never following redirects.

    A reply body is read up to ``max_body_bytes`` and one byte more, so a caller can tell an
    oversized reply from one that fits.
    """

    def __init__(
        self, origins: tuple[str, ...], provider: str, max_body_bytes: int = MAX_BODY_BYTES
    ) -> None:
        self._origins = origins
        self._provider = provider
        self._max_body_bytes = max_body_bytes
        self._opener = urllib.request.build_opener(NoRedirects)

    def send(self, request: HttpRequest) -> HttpResponse:
        if not any(request.url.startswith(origin + "/") for origin in self._origins):
            raise ValueError(f"requests may only go to {', '.join(self._origins)}")
        outgoing = urllib.request.Request(
            request.url, data=request.body, headers=dict(request.headers), method=request.method
        )
        try:
            try:
                with self._opener.open(outgoing, timeout=request.timeout) as reply:
                    body = reply.read(self._max_body_bytes + 1)
                    if len(body) <= self._max_body_bytes and getattr(reply, "length", None):
                        # The reply ended before the length it announced.
                        raise OSError(f"the {self._provider} reply was cut short")
                    return HttpResponse(
                        reply.status, {k.lower(): v for k, v in reply.headers.items()}, body
                    )
            except urllib.error.HTTPError as error:
                return HttpResponse(
                    error.code,
                    {k.lower(): v for k, v in error.headers.items()},
                    error.read(self._max_body_bytes + 1),
                )
        except (http.client.HTTPException, ValueError):
            # Not chained: these errors can quote the request's headers, token included.
            raise OSError(
                f"the {self._provider} call failed before a complete reply arrived"
            ) from None


def echoes(response: HttpResponse, secret: str) -> bool:
    """Whether the reply repeats the credential: in a header, the raw body or any JSON string."""
    if any(secret in value for value in response.headers.values()):
        return True
    if secret.encode() in response.body:
        return True
    try:
        pending: list[object] = [json.loads(response.body)]
    except (ValueError, RecursionError):
        return False
    while pending:  # iterative, so a deep reply cannot exhaust the stack
        item = pending.pop()
        if isinstance(item, str):
            if secret in item:
                return True
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False
