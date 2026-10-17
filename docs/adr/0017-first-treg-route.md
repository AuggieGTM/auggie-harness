# ADR 0017: First Treg route: page text over HTTP

- **Status:** Accepted
- **Date:** 2026-10-17

## Context

[ADR 0008](0008-treg-for-acquisition.md) puts Treg behind an acquisition adapter and enables its
routes one at a time. The first route must fetch the text of one company page (a careers or about
page) and record it as `SourceEvidence`. Treg's published documentation
([integration guide](https://treg.to/integrate.md), [reference](https://treg.to/docs),
[catalog](https://treg.to/catalog/endpoints/exa.web.contents.get)) describes three interfaces
(HTTP, MCP and a CLI) and many page-fetching routes, some of which choose a provider at run time.

## Decision

- **Python calls Treg over HTTPS**, on the fixed origin `https://treg.to`, with
  `POST /call/exa.web.contents.get`. The customer's org-scoped agent token goes in
  `X-Treg-Token`, read from the scope's credentials ([ADR 0016](0016-credentials-and-spend-limits.md)).
  The token is never sent as `Authorization`, which Treg may relay to the provider.
- **One Treg organization per customer**, each with its own agent token: the organization is
  the boundary Treg treats as hard (balance, history, secrets). Each organization opts out of
  Treg's shared archive.
- **The request is pinned in code:** one URL, page text bounded by `maxCharacters`, and no
  summaries, highlights or subpages. Summaries are model-generated, so they are never evidence.
  Each call sends a per-call cost ceiling and an `Idempotency-Key` for that acquisition attempt,
  reused only to recover a lost response. There is no retry or fallback to another provider
  inside the adapter. Changing the route or provider is a reviewed code change.
- **Mapping to `SourceEvidence`:**

  | Field | Source |
  |---|---|
  | `source_url` | The URL requested |
  | `provider_request_id` | Exa's `requestId` |
  | `observed_at` | When the adapter received the response, in UTC |
  | `published_at` | The provider's publication date if given; otherwise empty |
  | `final_url` | Empty: the documentation does not confirm the returned URL is the post-redirect one |
  | `excerpt` | The returned text, bounded to the contract's 4,000 characters |
  | `content_hash` | SHA-256 of the exact returned text, as UTF-8 |

  A failed, blocked or empty result, or a 402, 429, 503 or timeout, is recorded as a retrieval
  with its reason and no content. Treg's call ID, the settled cost and the cache status go on
  the `evidence.recorded` event.
- **Only the bounded excerpt and the hash are stored.** Full page text is not kept until Treg's
  and Exa's retention and usage terms are confirmed in writing. Treg's published privacy terms
  are inconsistent about whether response bodies are stored, and Exa's terms were not reviewed.
- **Tests in this repository replay synthetic responses** in the documented shape. Real pages,
  responses and credentials are never committed. A live acceptance call is made only by an
  operator with credentials and a run budget, outside CI, and its results stay out of the
  repository ([ADR 0010](0010-public-repository-boundary.md)).

## Consequences

- For real evidence, `content_hash` covers the returned text, of which the excerpt may be only
  part, so the hash cannot always be re-checked from stored data. Synthetic fixtures, whose
  snapshot is the excerpt, are unaffected.
- Catalog IDs name capabilities, not fixed versions. Drift is caught when the pinned request and
  response shapes stop matching, and is fixed by a reviewed change.
- The terms questions above must be settled before any customer's real accounts are researched.
