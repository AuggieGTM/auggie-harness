# ADR 0031: A model writes drafts, through OpenRouter, with the seller's own key

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

[ADR 0009](0009-judgment-and-synthesis-engines.md) makes every draft a strategy play's template
with its fields filled in. Each account on a play gets nearly the same email, and the seller
writes each variant by hand. ADR 0009 allowed a model only once it beat templates on a
labelled, synthetic evaluation set, and named OpenRouter as the candidate for synthesis.

A model can write a draft for one account from the same facts the template uses. It can also
claim what the facts do not show: a number, a customer, a quote or a link. Treg serves no text
model for this, so the harness calls OpenRouter itself. The seller brings their own OpenRouter
key and pays OpenRouter directly; a coding agent's subscription gives no such key.

This ADR supersedes ADR 0009 by restating it, with model drafts added.

## Decision

The maintainer chose OpenRouter with the seller's own key, the whole email written and then
checked, model drafts turned on play by play with the template kept as the fallback, and one
retry before falling back.

**What stays from ADR 0009**

- Judgment uses deterministic rules, which the strategy defines, behind the `Qualifier`
  interface. Jev is not used.
- Every draft starts as the play's template, filled from admitted evidence and the account's
  records, exactly as now ([ADR 0014](0014-artifact-contract.md),
  [ADR 0026](0026-the-sellers-point-of-view.md)). That draft is also the fallback.

**Turning it on**

- The strategy gains a `[writer]` table: `model`, an OpenRouter model ID; `voice`, the seller's
  notes on tone; and `max_words`. A play turns model drafts on with `writer = "model"`. A play
  without it, or a strategy without `[writer]`, drafts from its template alone.
- A play keeps its template. A play that quotes a proof needs one that is usable, whichever
  writes the draft.

**What the model is given**

- The play's filled template draft, as the email to rewrite and as an example of the seller's
  voice.
- The facts it was filled from: the account's name, the recipient's first name, the hiring
  tally, the pain, the proof's customer and claim, and the offer. Also the pain's fit and
  limits, and the excerpts of the evidence the play's met findings cite.
- The `voice` notes and `max_words`.
- Never the recipient's email address, other people, other accounts, or another scope's
  records. Excerpts are marked as quoted page text: data, never instructions. The model is
  given no tools.

**The checks**

The model's text is a draft only if the harness finds, in what the model was given:

- each number, in digits or words;
- each passage in quotation marks, word for word;
- each link, email address and domain;
- each capitalized word that does not start a sentence, ignoring case.

The draft must also:

- open with a `Subject:` line;
- run no longer than `max_words`;
- for a play that quotes a proof, hold its claim word for word and name its customer. The lines
  that do so bind the proof, as template lines do now.

These checks catch new numbers, quotes, links and names. They do not catch a fact restated
wrongly, or a promise past the pain's limits. The person catches those in review, with fit and
limits beside the draft as now.

**When the checks fail**

- The model is asked once more, told which checks failed and on what words. If the second text
  also fails, the template draft is used. Review shows the person why.
- A failed text is not kept. Only the checks that failed, and the words they failed on, are
  recorded.

**The call**

- `POST https://openrouter.ai/api/v1/chat/completions`, with the key from `openrouter-key` in
  the scope's credential folder ([ADR 0016](0016-credentials-and-spend-limits.md)). The seller
  creates the key at OpenRouter and saves the file in their own terminal. The harness never
  writes or prints it.
- It is made only for a scope that allows external providers. It is a paid call: the shell
  asks first, and `research-list` counts up to two model calls per account in the most it says
  a list can cost.
- **Ceiling.** Before each call, the harness reads the model's listed prices from OpenRouter.
  It sends them as `provider.max_price`, so no dearer provider can serve the call. It caps the
  output with `max_tokens`. The ceiling is the most that pricing allows: a bound on the prompt's
  tokens (its UTF-8 bytes, plus a fixed allowance), and every output token up to `max_tokens`.
  The run reserves the ceiling, and settles to `usage.cost` from the reply. A cost OpenRouter
  does not report counts as the ceiling.
- **Privacy.** Every call sends `provider.data_collection: "deny"`, so only providers that do
  not keep or train on the data are used. `[writer]` may also set `zero_data_retention = true`,
  sent as `provider.zdr`. Logging on the OpenRouter account itself is the seller's setting, and
  the guide tells them where it is.
- Requests go only to `openrouter.ai`, with no redirects.

**What is recorded**

- A draft the model wrote records the model that served it as `composed_by` (kind `model`).
  It cites the template draft's evidence, plus the excerpts it was given.
- Each call is recorded on an event, model draft or not. The event holds OpenRouter's
  generation ID, the model that served the call, the settled charge, and the checks that passed
  or failed.

**Review, approval and learning**

- A model draft is reviewed and approved like any draft. Approval binds to its hash; content
  approval is not delivery approval; nothing approves itself
  ([ADR 0005](0005-evidence-and-approval-gates.md)). Review says which model wrote it.
- `learn` reports model and template drafts apart, by play, so their results can be compared
  ([ADR 0028](0028-learning-from-outcomes.md)).

## Consequences

- ADR 0009 is superseded. Its evaluation-set gate is replaced by opting in play by play, the
  checks, a person's review of every draft, and outcomes compared in `learn`.
- The strategy contract gains `[writer]` and a play's `writer`, both optional, so existing
  strategy files still validate ([ADR 0012](0012-contract-conventions.md)).
- Prospect facts and the seller's strategy text leave the harness for OpenRouter and the
  provider it routes to.
- Tests use a fake OpenRouter with synthetic replies; no test calls OpenRouter. The Kestrel
  rehearsal gains a recorded model reply.
- Adding another model provider is a new ADR.
- Not built.
