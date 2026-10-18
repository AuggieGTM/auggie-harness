# Synthetic listing replies

Invented replies in the shapes Lever, Greenhouse and Ashby document for their public job-board
feeds (ADR 0024), used to test the careers readers without network access. The companies, roles
and IDs are made up, and every link uses a reserved `.example` domain in place of the services'
own hosted pages. Nothing here came from a real read. Each file holds the HTTP `status`, the
lower-case `headers` and the `body` (JSON, or a string sent as-is).

- `lever-fernhollow.json`, `greenhouse-brightquarry.json` and `ashby-saltmarsh.json` are
  complete listings. Each also carries the date fields the services send but do not document as
  a posting date (`createdAt`, `updated_at`, `publishedAt`), which the readers leave out.
- `ashby-saltmarsh.json` holds one job with `isListed` false, which is not in the listing.
- `greenhouse-total-mismatch.json` states a total its jobs do not match.
- `not-found-404.json` and `malformed.json` are failed reads.
