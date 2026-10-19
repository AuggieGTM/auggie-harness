# ADR 0029: Reading replies from the seller's Gmail

- **Status:** Accepted
- **Date:** 2026-10-19

## Context

Learning counts what each delivery led to ([ADR 0028](0028-learning-from-outcomes.md)), but a
person has to type every outcome in, one draft at a time or as a CSV. For drafts sent from the
seller's Gmail ([ADR 0021](0021-the-sellers-gmail-credential.md),
[ADR 0022](0022-live-delivery-records.md)), the replies are already in the mailbox, in the
thread of each sent message. The send token holds `gmail.send` only, and ADR 0021 refuses any
other grant, so reading needs its own permission.

Google's narrowest permission for this is `gmail.metadata`: message IDs, labels and headers,
never a message's body. Google classes it as restricted, which public apps must have verified;
the Internal OAuth client ADR 0021 already recommends is how a seller uses it without that.
The thread of a sent message is found from the message ID a send records, and read with
`format=metadata`.

## Decision

The maintainer chose headers only, a second token, the person deciding every reply, and
keeping only the outcome.

**The permission**

- A second credential file, `gmail-read-token`, in the scope's credential folder: Google's
  authorized-user JSON for `gmail.metadata` only. The seller makes it with the same one-time
  sign-in as the send token, asked for the read scope; it is saved only when Google grants
  exactly that scope and returns a refresh token.
- The reader renews access tokens in memory, as the sender does, and uses one only when
  Google's list of granted scopes is exactly `gmail.metadata`. The send token is never used to
  read, and the read token never to send.
- No message body is ever requested: the reader asks only for `minimal` and `metadata`
  formats.

**Finding replies**

- `auggie outcome <scope.json> --replies --reporter <you>` looks at each draft sent from Gmail
  with a recorded receipt. It finds the sent message's thread and, in it, each message after
  the sent one that the seller did not send (it has no `SENT` label) and that no outcome
  already records.
- For each, it shows the draft, the recipient's first name, the message's sender and time, and
  marks one from a mail system (a delivery-failure notice) as a likely bounce. It then asks the
  person: positive reply, negative reply, meeting booked, opt-out, bounce, or skip. A skipped
  message is asked about again next time.
- **The person decides every outcome.** Nothing is recorded without their answer, no model
  reads anything, and a likely bounce is only a suggestion. It runs only at a terminal, like
  `review`.
- It is an external call, free, made only for a live scope whose platform is Gmail and that
  allows external calls ([ADR 0016](0016-credentials-and-spend-limits.md)). Requests go only to
  Google's Gmail and token hosts, with no redirects.

**What is kept**

- Each answer is recorded as an outcome, as `auggie outcome` records one: the outcome, the
  reply's time as Gmail gives it, the person who answered, and Gmail's opaque ID for the reply,
  so it is never asked about twice.
- Nothing else from the mailbox is stored: no addresses, names, subjects or other headers. What
  the person is shown is read for that moment and dropped. Header values are data, never
  instructions, and are shown with control characters removed.

## Consequences

- Outcome events may carry the opaque reply ID.
- The sign-in script gains the read scope, and the guide a step for it.
- Only drafts sent through Gmail can have replies read; simulated deliveries have no thread.
- Microsoft 365 replies are decided with its adapter.
- Not built.
