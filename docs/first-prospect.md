# Your first prospect

This guide is for a seller prospecting for the company they work at. It takes you from a fresh
clone to one of your prospects researched, drafted, reviewed and (simulated) delivered. Part 1
rehearses the whole loop offline on the synthetic Northstar prospect, at no cost and with no
accounts. Part 2 does it for real.

Each step is a separate command that you run when you are ready. Nothing runs in the background,
and nothing is sent: delivery stays simulated until the delivery adapters exist.

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

The workbook holds the four tabs a sheet would show (accounts, findings, evidence and drafts),
without page text. Write it outside the clone, so it never sits in this repository's working
tree. To see it in a sheet, follow
[Writing a projection with your agent](../README.md#writing-a-projection-with-your-agent).

Try the refusals too, each on a fresh `research` run:

- Answer `c` (changes) at review, and `deliver` is refused. Once a reviewer asks for changes,
  that version can never be delivered, even if it is approved later. There is no command to edit
  a draft yet, so research again for a new one.
- Approve the content but not delivery, and `deliver` is refused until a delivery approval
  exists.
- Deliver twice, and nothing is sent the second time.

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
- `recipient`: the person's ID in your CRM and their first name.

Keep it to what your company is allowed to hold about the prospect.

### 5. Run it

The same commands as Part 1, with your files, your MongoDB, and no `--replay`:

```bash
uv run auggie research <scope.json> <prospect.json> \
  --strategy-repo <your strategy repo> --strategy-path <path in it> \
  --operator "your name" --mongodb <uri>
uv run auggie review <scope.json> --draft <draft id> --reviewer "your name" --mongodb <uri>
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
- Nothing is sent. `deliver` records a simulated hand-off, and only after both approvals.
- Research the same prospect again later and you get a new run with new records. Earlier runs
  are kept.
