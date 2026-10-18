# ADR 0021: The seller's Gmail credential

- **Status:** Proposed
- **Date:** 2026-10-18

## Context

[ADR 0020](0020-one-delivery-adapter-per-sending-platform.md) sends from the seller's own
mailbox, Gmail first, with the send-only `gmail.send` scope. Gmail's API takes OAuth access
tokens, which last a short time and are renewed with a refresh token.
[ADR 0016](0016-credentials-and-spend-limits.md) already decides where credentials live: a file
per secret in the scope's own folder, created by the deployment, read by the harness and never
written by it, with no shared or logged-in-CLI fallback. What is open is how a seller gets a
refresh token into that folder, and what happens when it stops working.

Google's documentation sets the constraints:

- A program on the seller's own computer uses an OAuth client of type **Desktop app**, signs in
  through a loopback redirect with PKCE, and always receives a refresh token. Desktop clients
  are public clients: their client secret is not confidential, but the refresh token is.
- A refresh token stops working when the person revokes the app's access, when it goes unused
  for six months, when the person changes their password (for tokens with Gmail scopes), when
  an administrator restricts the service or a session-length policy ends it, or when the
  account passes 100 live tokens for one client, which silently invalidates the oldest.
- A project with an **External** user type in **Testing** status gets refresh tokens that
  expire after 7 days. An app that asks for a sensitive scope such as `gmail.send` must pass
  Google's verification before it is published, but internal apps, used only within one Google
  Workspace organization, and apps still in development do not need it.

The options considered:

1. **The seller signs in once, in their own terminal, with a documented command** that writes
   the authorized-user file (client ID, client secret, refresh token) into the scope's folder.
   The harness reads it and renews access tokens in memory. ADR 0016 stands as it is.
2. **The harness runs the sign-in and writes the token itself** (an `auggie` sign-in command).
   Friendlier, but it means amending ADR 0016's rule that the harness never writes secrets.
3. **A logged-in CLI's credentials, such as gcloud's application default credentials.** Ruled
   out by ADR 0016: no logged-in-CLI fallback.
4. **The agent's Gmail connector.** Ruled out by
   [ADR 0019](0019-projection-writers-and-agent-connectors.md) and ADR 0020: connectors never
   deliver for the harness.
5. **A short-lived access token handed over for each session.** Nothing long-lived on disk, but
   the seller would mint and pass a token every hour of work.

## Decision (proposed)

- Option 1. The Gmail credential is one file, `gmail-token`, in the scope's credential folder:
  Google's authorized-user JSON for the `gmail.send` scope only. The seller creates it with a
  one-time sign-in that the guide documents and that runs in their own terminal, outside any
  agent session. Like the Treg token, it is written to a new private file that then replaces
  whatever was at the path.
- The harness reads the file, renews access tokens in memory, and never writes the file back.
  Renewing an expired or revoked credential means signing in again, which replaces the file.
- The adapter refuses a token that Google reports with any scope beyond `gmail.send`.
- When Google refuses to renew the token, nothing is sent. Delivery stops with a reason that
  tells the seller to sign in again. Since nothing was sent, this is a known failure, not an
  unknown send under ADR 0020, and delivery can be tried again after signing in.
- The supported OAuth client is a Desktop app client in the seller's company's Google Cloud
  organization, with the **Internal** user type: it needs no verification, and its tokens are
  not limited to 7 days. An External client in Testing status is enough to try it out, with
  tokens that last 7 days. Publishing a verified External app is not covered.
- Microsoft 365's credential is decided with its adapter.

## Consequences

- A seller needs their company's Google Workspace administrators to allow an internal OAuth
  client, or to create one for them. Without it, the 7-day Testing path or simulated delivery
  remains.
- The file can send email as the seller. It is never printed or recorded (ADR 0016). If it
  leaks, the seller removes the app's access in their Google Account, which revokes it.
- The sign-in needs Google's OAuth library only for that one command. The adapter itself uses
  `google-auth`, which the `google` extra already installs.
- The agent prompts leave the sign-in to the person, as they do for Treg tokens.
- Not built. Delivery stays simulated.
