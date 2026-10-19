# Your first prospect

This guide is for a seller prospecting for the company they work at. It takes you from a fresh
clone to one of your prospects researched, drafted, reviewed and (simulated) delivered. Part 1
rehearses the whole loop offline on the synthetic Northstar prospect, at no cost and with no
accounts. Part 2 does it for real.

Each step is a separate command that you run when you are ready. Nothing runs in the background.
Delivery is simulated, so nothing is sent, until you set up sending from your Gmail
([Part 3](#part-3-sending-from-your-gmail)). Run the commands for a scope one at a time, each
finishing before the next starts: its records have a single writer
([ADR 0015](adr/0015-mongodb-store-layout.md)).

## Before you start

- Python 3.12+ and [uv](https://docs.astral.sh/uv/), Git, and Podman or Docker.
- Bash. The commands below are written for it; on a Mac, where the default shell is zsh, run
  `bash` first.
- In the clone: `uv sync`. It installs the MongoDB and Google extras too.
- For Part 2: the [Treg](https://treg.to) CLI, and optionally an agent (Claude, ChatGPT) with a
  Google Sheets connector.

## Part 1: rehearse offline

Start a local MongoDB for the records. Set `P` to a free local port; 27017 is MongoDB's usual
one. If you already started `auggie-mongo` from the README, use its port and skip `podman run`.

```bash
P=27017
podman run -d --rm --name auggie-mongo -p 127.0.0.1:$P:27017 docker.io/library/mongo:8
```

All the files come from `examples/synthetic/northstar-msp/`: a synthetic scope, a
synthetic prospect, the strategy, and recorded source responses.

```bash
E=examples/synthetic/northstar-msp
M="--mongodb mongodb://127.0.0.1:$P --database-prefix rehearsal_"

uv run auggie research $E/scope.json $E/prospect.json \
  --strategy-repo . --strategy-path $E/inputs/strategy/msp.toml \
  --operator "your name" --replay $E/inputs/inputs.json $M
```

`research` prints each stage: the strategy and the exact commit it was read at, every source it
fetched (the news source fails on purpose), which evidence it admitted, and the qualification
with the evidence behind each finding. It stops at the draft and prints its ID; add
`--show-draft` to see the text now, or wait for `review`, which always shows it. A rehearsal is
judged as of the moment its responses were recorded, so it gives the same result on any date.

```bash
uv run auggie review $E/scope.json --draft <draft id> --reviewer "your name" $M
```

`review` shows the exact draft, how the prospect was judged, and the pages it rests on. Approve
the content, then delivery to the named recipient. It records your decision as a person's
approval, so run it yourself, at your own terminal. It refuses redirected input or output, and
it is not a step to hand to an agent. Then:

```bash
uv run auggie deliver $E/scope.json --draft <draft id> $M
uv run auggie outcome $E/scope.json --draft <draft id> --outcome positive_reply \
  --reporter "your name" $M
uv run auggie project $E/scope.json $M --workbook ~/auggie-rehearsal.json
```

The workbook holds the six tabs a sheet would show (accounts, findings, evidence, drafts,
hiring and learning), without page text. Write it outside the clone, so it never sits in this
repository's working tree. To see it in a sheet, follow
[Writing a projection with your agent](../README.md#writing-a-projection-with-your-agent).

Try the refusals too, each on a fresh `research` run:

- Answer `c` (changes) at review, and `deliver` is refused. Once a reviewer asks for changes,
  that version can never be delivered, even if it is approved later. Revise it instead:

  ```bash
  uv run auggie revise $E/scope.json --draft <draft id> --author "your name" $M
  ```

  `revise` shows the reviewer's note, opens the draft in your editor (`$VISUAL` or `$EDITOR`,
  else `vi`) and stores what you save as the draft's next version. A graphical editor must wait
  until you close the file, as `code --wait` does. If `revise` stops partway, the next `review`
  or `revise` of that draft finishes recording it. Like `review`, it runs only
  at a terminal. Review the new version as you did the first: no earlier approval carries over.
- Approve the content but not delivery, and `deliver` is refused until a delivery approval
  exists.
- Deliver twice, and nothing is sent the second time.

A second synthetic seller, a security vendor, counts hiring from a careers listing instead of
reading a careers page. `uv run auggie run examples/synthetic/kestrel-security` runs its whole
scenario in memory: it qualifies an account from detection engineering roles on a replayed
listing, and compares that listing with one read 35 days earlier.

When you are done, `podman stop auggie-mongo` stops MongoDB. It was started with `--rm`, so
stopping it also deletes the rehearsal records.

## Part 2: your first real prospect

Everything in this part stays **outside this repository**: your scope, strategy, credentials,
prospects and records ([ADR 0010](adr/0010-public-repository-boundary.md)). In the harness's
terms, the company you sell for is the *customer*: `customer_id` and the scope name it.

### 1. Your scope

Copy `examples/synthetic/northstar-msp/scope.json` somewhere private and change it:

- `customer_id`: a short ID, such as your company's name. The three namespaces: names used by
  no other scope on the same MongoDB or credentials folder
  ([ADR 0004](adr/0004-customer-isolation.md)).
- `"synthetic": false`.
- `"execution"`: `"delivery": "simulated"`, `"external_providers": true`, and
  `"max_run_spend_micro_usd"`, the most one run may spend on paid calls. Each page fetch reserves
  2,000 (the $0.002 per-call ceiling), so 20,000 allows ten fetches
  ([ADR 0016](adr/0016-credentials-and-spend-limits.md)).

### 2. Your strategy

The strategy is a TOML file in a private Git repository of your own (a local one is enough)
([ADR 0013](adr/0013-strategy-files.md)). Start from
`examples/synthetic/northstar-msp/inputs/strategy/msp.toml`. Set `customer_id`, give the
revision a new `strategy_id`, and adapt the sources, criteria and play to your ideal prospect
and the wording you use. If your company approves outreach wording, have it reviewed, then
commit. `research` reads the file exactly as committed, so uncommitted edits never reach a run.

To give a draft your point of view ([ADR 0026](adr/0026-the-sellers-point-of-view.md)), the
security seller's `examples/synthetic/kestrel-security/inputs/strategy/security.toml` shows
the shape:

- `[proofs.<id>]` is one customer story: the customer, the one public page of yours that tells
  it, the exact sentence a draft may quote from that page, `result` or `scale`, and an optional
  caveat that review shows. `[proofs]` sets `max_age_days`, how old a read of the page may be
  (default 180).
- `[pains.<id>]` is an angle: a problem teams like your prospects often have, where you fit,
  where you stop, the one offer a draft closes on, and its proofs, best first. An optional
  metric, threshold and consequence are shown at review only. Write the statement as an angle,
  never as a fact about one account.
- A play that names a `pain` may use `{pain}`, `{offer}`, `{proof_claim}` and
  `{proof_customer}`. One that names a `hiring` criterion it requires may use
  `{hiring_count}`, `{hiring_family}` and `{hiring_date}`. A play that quotes a proof is used
  only while one of its pain's proofs is usable, so put a play without a proof after it.

A proof is usable only while the newest read of its page was admitted, is no older than
`max_age_days`, and holds the claim and the customer's name word for word, case included. After
each change to your proofs, read their pages, a paid Treg call of at most $0.002 a page:

```bash
uv run auggie proofs <scope.json> --mongodb <uri> --read \
  --strategy-repo <your strategy repo> --strategy-path <path in it>
```

`auggie proofs` without `--read` lists each proof and why it is or is not usable. Research reads
no proof pages: it uses the reads already recorded. A read keeps the first 4,000 characters of
the page's text, so a claim further down cannot be checked; quote one near the top. Your case
studies are your company's claims, so `review` shows each quoted proof as a vendor claim, with
its page, when it was read and its caveat, beside the pain's fit and limits. If `revise` changes
the quoted words, the new version is no longer shown as quoting the proof, and review judges
its text as yours.

### 3. A Treg team and token for the harness

Give the harness a Treg team of its own ([ADR 0017](adr/0017-first-treg-route.md)), so its
spending and its token stay apart from your other Treg use. Run these **in your own terminal,
not inside an agent session that is recorded or shared**: `treg` prints tokens.

```bash
treg org create "<team name>"
treg org agent-new auggie-harness --cap 50 --pin customer=<customer_id>
```

`treg org create` makes the new team the active one, so `agent-new` mints the token in it.

Save the agent token where the harness looks for it, readable only by you. Paste it at the
hidden prompt. It is written to a new private file, which then replaces whatever was at the path,
symlink included, so old permissions or a link cannot leak it:

```bash
( D=~/.config/auggie/credentials/<credential_namespace>; T=; umask 077
  mkdir -p "$D" && chmod 700 "$D" && read -rs TOKEN &&
  T=$(mktemp "$D/.treg-token.XXXXXX") && printf '%s\n' "$TOKEN" > "$T" &&
  python3 -c 'import os, sys; os.replace(sys.argv[1], sys.argv[2])' "$T" "$D/treg-token" ||
  { [ -z "$T" ] || rm -f "$T"; false; } )
```

Then add funds to the team (`treg topup 10`; $10 is the minimum) and switch back to the team
you usually use: `treg org ls` lists your teams' slugs, and `treg org use <slug>` switches.

Before researching real prospects, confirm two things. Treg's and Exa's terms must allow
keeping the excerpts the harness stores ([ADR 0017](adr/0017-first-treg-route.md)). Your
company's policies must allow sending prospect page URLs to them.

Then check the token. `uv run auggie treg-fetch <scope.json> https://example.com/` makes one
paid call, of at most $0.002, and stores nothing.

### 4. The prospect

Write a `prospect.json` like the synthetic one:

- `account_id`, `customer_id` (the scope's), and a short `key` used in record IDs.
- The prospect company's `name`.
- `sources`: one URL for each source kind the strategy names (`careers`, `technology`, ...).
- `recipient`: the person's ID in your CRM, their first name and, to send from Gmail, their
  `email`. Research records each person once, exactly as given, so a new address needs a new
  person ID.
- `careers_listing`, if your strategy counts hiring: the listing's `service` (`lever`,
  `lever-eu`, `greenhouse` or `ashby`), the company's `board` name as the service spells it in
  the listing's address (jobs.lever.co/*board*, jobs.eu.lever.co/*board* for Lever's EU
  instance, boards.greenhouse.io/*board*, jobs.ashbyhq.com/*board*), and the `url` of that
  page. Check that it is the company's own
  listing: the harness reads only the listing you name, and never looks for another when it
  fails.

Research reads a named listing whole, for free, from the service's public job-board API, and
records what it showed as a careers snapshot. A read that fails is recorded too, and the
hiring criteria it would have settled stay unknown. Before the first live read, check that
service's terms of use and your company's policy, as you did for Treg
([ADR 0024](adr/0024-careers-snapshots-and-hiring-tallies.md)). None of the three services says
when a role was posted, so the long-listed pattern is never met: it stays unknown for a family
with open roles, and only a complete read with none rules it out.

Keep it to what your company is allowed to hold about the prospect.

### 5. Run it

The same commands as Part 1, with your files, your MongoDB, and no `--replay`:

```bash
uv run auggie research <scope.json> <prospect.json> \
  --strategy-repo <your strategy repo> --strategy-path <path in it> \
  --operator "your name" --mongodb <uri>
uv run auggie review <scope.json> --draft <draft id> --reviewer "your name" --mongodb <uri>
# only if review asked for changes, then review again:
uv run auggie revise <scope.json> --draft <draft id> --author "your name" --mongodb <uri>
uv run auggie deliver <scope.json> --draft <draft id> --mongodb <uri>
uv run auggie outcome <scope.json> --draft <draft id> --outcome <what happened> \
  --reporter "your name" --mongodb <uri>
uv run auggie project <scope.json> --mongodb <uri> --workbook <file outside the clone>
```

Each `research` run fetches one URL from `prospect.json` for each source kind the strategy
names, and ignores any others. It may spend up to $0.002 for each, within the scope's per-run
limit.

### What to check

- A source that fails is recorded with its reason and leaves its criteria **unknown**. It never
  counts against the prospect.
- Every finding cites the evidence it rests on. A draft uses only admitted evidence, and the
  rest of its text is your strategy's play.
- With a simulated scope nothing is sent: `deliver` records a simulated hand-off, and only
  after both approvals.
- Research the same prospect again later and you get a new run with new records. Earlier runs
  are kept.

## Asking for things: the research shell

Instead of one command per step, you can work in a shell for one scope
([ADR 0025](adr/0025-the-research-shell.md)):

```bash
uv run auggie shell <scope.json> --mongodb <uri> --me "your name" --prospects <folder> \
  --strategy-repo <your strategy repo> --strategy-path <path in it>
```

`--prospects` is a folder of prospect files, one per account. Add `--replay <file>` to rehearse
with recorded responses. At the `auggie>` prompt:

- `accounts`, `brief <account>`, `find [<account>] <words>`, `stack [<account>]`, `hiring
  [<account>]` and `proofs` answer from the records. Each answer names its records and when
  their sources were read, and reads or spends nothing. `hiring` shows each account's newest
  listing read: its roles per family of your strategy, with their levels, and the listing
  patterns that hold. A newer read that failed is shown as failed; an older one never stands in
  for it. `proofs` shows each of your proofs, and whether a draft may quote it.
- `fetch <account> <url>` reads one page you name and records it as evidence. `research <account>`
  researches from the account's prospect file, and `research --hiring` screens every prospect file
  by hiring first, then researches the ones that pass and offers to review their drafts. When they
  read live, each says what it will call and the most it can spend, and waits for your yes.
  `hiring <account> --read` reads the account's careers listing now, free, and asks the same way,
  as does `proofs --read`, which reads your proof pages through Treg. With `--replay` nothing is
  called, so nothing is asked.
- `find` searches every stored account page and labels any that were not admitted, so you can
  tell rejected or unchecked text from evidence. A quote is the page's own words, with its
  whitespace collapsed and, when its matches are far apart, an ellipsis between two passages.
- No answer exports a recipient's person record or its email address; `review` is where that
  address is shown. Page text, URLs and rationales are not filtered, so they show any address
  the page itself contains.
- `review`, `revise`, `deliver`, `outcome` and `resolve` run the flow, as the single commands do.
  The shell runs only at your own terminal, because approvals and edits happen in it.

The questions also work as single commands that print JSON, for your coding agent: `auggie
accounts`, `auggie brief`, `auggie find`, `auggie stack`, `auggie hiring` and `auggie proofs`,
each with `--json`. They only read, so an agent can run them and answer you by quoting the
records. `auggie hiring --account <id> --read` and `auggie proofs --read` read first and record
what they read, so they print no JSON.

## Many prospects: a list in one run

Once one prospect works, you can bring in a list
([ADR 0027](adr/0027-many-accounts.md)). Rehearse it first with the security seller's
synthetic list, with the `M` from Part 1:

```bash
K=examples/synthetic/kestrel-security
S="--strategy-repo . --strategy-path $K/inputs/strategy/security.toml"
uv run auggie import $K/scope.json $K/prospects.csv --prospects ~/auggie-prospects $S
uv run auggie import $K/scope.json $K/prospects.csv --prospects ~/auggie-prospects $S --write
uv run auggie research-list $K/scope.json --prospects ~/auggie-prospects $S \
  --operator "your name" --replay $K/list-replay.json $M
uv run auggie review $K/scope.json --next --reviewer "your name" $M
```

- `import` reads a CSV with a header row and shows what each row becomes, or why it cannot be
  one. The columns are `name` and `first_name` (required), `key`, `person_id` and `email`, one
  column per source kind your strategy names, and `listing_service`, `listing_board` and
  `listing_url` together. Any other column refuses the whole list, since it is probably a typo.
  Only `--write` writes, and only new files: a file that already exists is never replaced. The
  files hold your recipients' names and addresses, so keep the folder outside the clone. The
  harness creates it readable by you alone, and refuses to write into a folder others can
  read.
- `research-list` researches every prospect file in the folder, in file-name order, in one run.
  The whole list shares one spending limit: your scope's per-run limit, or a lower
  `--limit`. An account starts only when its worst case, at most $0.002 for each page it
  names, fits what is left; the rest wait for your next run. Accounts already researched are
  skipped unless you add `--again`, so an interrupted list resumes without paying again for
  the accounts it finished. An account that stopped partway is researched again from its
  start, and its pages are paid for again. An error stops the list, and its report says what
  was researched, skipped and waiting, and what each account spent. Two prospect files that
  hold one account differently are both left out, and the report names them.
- `research-list --screen` reads every careers listing on the list first, for free, and
  researches only the accounts hiring as your strategy requires
  ([ADR 0030](adr/0030-screening-a-list-by-hiring.md)). An account shown not to be hiring is
  screened out; one with no listing, or a read that settles nothing, is listed as not screened,
  for you to research by hand. Only the accounts that pass are paid for.
- `review --next` shows the oldest draft waiting for you, then asks whether to go on. Each draft
  is still reviewed and approved on its own.

## Learning from what happened

Record what each delivery led to, one at a time with `auggie outcome --draft`, or from a list
([ADR 0028](adr/0028-learning-from-outcomes.md)):

```bash
uv run auggie outcome <scope.json> --from outcomes.csv --reporter "your name" $M
uv run auggie outcome <scope.json> --from outcomes.csv --reporter "your name" $M --write
```

The CSV has the columns `draft`, `outcome` and `occurred_at` (a date, or a time with its zone).
The first command previews each row against your records: new, already recorded, or why it
cannot be, such as a draft never delivered or a time before its delivery. Only `--write`
records.

`auggie learn <scope.json> $M` then shows, by play, pain, proof and met criterion, how many
deliveries were positive (a reply or a meeting), negative (a negative reply, an opt-out or no
reply), bounced or still waiting. A rate is positive deliveries over settled ones, so
deliveries still waiting never lower it. Add `--json` for your coding agent.

To have the harness propose changes to your strategy:

```bash
uv run auggie learn <scope.json> $M --strategy-repo <your strategy repo> \
  --strategy-path <path in it> --propose ~/strategy-proposal.patch
```

It proposes only a new order: your plays, and each pain's proofs, best first, among those
with at least 10 settled deliveries. It never writes new wording and never removes anything.
The patch is a file only you can read; review it, then `git apply` it in your strategy
repository and commit, or delete it. The harness never applies it. If your file's layout keeps
it from making a change safely, such as a proofs list over several lines, it tells you the
order to set by hand instead.

## Part 3: sending from your Gmail

The harness can send an approved draft from your own Gmail, one message per approval, with a
permission that can send mail but not read it
([ADR 0020](adr/0020-one-delivery-adapter-per-sending-platform.md),
[ADR 0021](adr/0021-the-sellers-gmail-credential.md),
[ADR 0022](adr/0022-live-delivery-records.md)). The adapter is tested against a fake Google
only, so send your first message to yourself.

1. **An OAuth client.** Your company's Google Workspace administrators create, or let you
   create, a Google Cloud project in the company's organization, with the Gmail API enabled, an
   OAuth consent screen whose audience is **Internal**, and an OAuth client of type
   **Desktop app**. Download the client's JSON file and keep it outside the clone. Google's
   [Gmail API Python quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python)
   walks through these steps. Where your company allows it, an External app in Testing status
   works too, but its tokens expire after at most 7 days.
2. **Sign in, in your own terminal**, not inside an agent session:

   ```bash
   uv run --with google-auth-oauthlib python scripts/gmail_sign_in.py \
     <scope.json> <client_secret.json>
   ```

   Google's consent page opens in your browser. Check the account it names: the harness sends
   as that account. The script saves the token as `gmail-token` in the scope's credential
   folder only if Google granted exactly `gmail.send`, and it prints no token. Sign in again
   whenever Google stops renewing the token, for example after a password change.
3. **The scope.** Set `"execution"` to `"delivery": "live"`, `"platform": "gmail"` and
   `"external_providers": true`. Give the prospect's recipient an `email`.
4. **Send.** Research and review as before. `review` now asks you to approve sending to the
   recorded address from your Gmail mailbox. Then:

   ```bash
   uv run auggie deliver <scope.json> --draft <draft id> --mongodb <uri>
   ```

   It prints Gmail's message ID. Delivering again sends nothing.

If `deliver` stops and says the message may have been sent, the harness cannot tell whether it
went out, and it never tries that draft again. Look in your Sent folder, then record what you
found, at your own terminal:

```bash
uv run auggie deliver <scope.json> --draft <draft id> --resolve sent \
  --reporter "your name" --mongodb <uri>
```

Use `--resolve not-sent` if it is not there. Either way the draft is closed. If it never went
out, research again for a new draft.

### Reading replies, headers only

Instead of typing each outcome, you can have the harness find the replies to drafts it sent
from your Gmail and ask you what each one was
([ADR 0029](adr/0029-reading-replies-from-gmail.md)). It needs a second token, separate from
the send token, that can read message headers but never a message's body:

```bash
uv run --with google-auth-oauthlib python scripts/gmail_sign_in.py --read \
  <scope.json> <client_secret.json>
```

It saves `gmail-read-token` only if Google granted exactly `gmail.metadata`. Google classes that
permission as restricted, so use the Internal OAuth client from step 1. Then, at your own
terminal:

```bash
uv run auggie outcome <scope.json> --replies --reporter "your name" --mongodb <uri>
```

For each message in a sent draft's thread that came after it and that you did not send, it
shows who it is from and when, marks a mail system's delivery-failure notice as a likely
bounce, and asks you: positive, negative, meeting, opt-out, bounce, or skip. Nothing is
recorded without one of those answers, and a skipped message is asked about again next time.
A reply is tied to the draft it answers; one in a thread with more than one of your drafts that
does not say which it answers is listed for you to record by hand, and a reply is never counted
for two drafts. The
harness keeps only the outcome you chose, the reply's time and Gmail's ID for it: no addresses,
names or subjects. It is tested against a fake Google only.
