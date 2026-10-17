# Synthetic Treg responses

Invented replies in the shape Treg documents for `exa.web.contents.get` (ADR 0017), used to
test the Treg adapter without network access. Every URL uses a reserved `.example` domain, and
the call and request IDs are made up. Nothing here came from a real call. Each file holds the
HTTP `status`, the lower-case `headers` and the `body` (JSON, or a string sent as-is).
