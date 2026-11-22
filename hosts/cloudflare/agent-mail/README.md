# agent-mail

Receive-only inboxes for the homelab's agents, on `agents.ninjaprivacy.org`. Agents use them for sign-up codes, verification links and invites (Bitwarden first). Agents never send mail from here.

Decision: vault `5-Decisions/2026-11-21_agent-mail-cloudflare-subdomain.md`. Project: vault `1-Projects/agent-accounts/`.

## How mail flows

```
sender ──SMTP──▶ Cloudflare MX (agents.ninjaprivacy.org)
                  │ SPF/DKIM/DMARC checked; mail failing the sender's DMARC policy is refused
                  │ one literal Email Routing rule per agent: <agent>@ → "send to Worker agent-mail"
                  │ subaddressing on: <agent>+<tag>@ matches the <agent>@ rule
                  ▼
                Worker email()  ── unknown local-part → reject "Address does not exist"
                  │                 > 10 MiB         → reject "Message too large"
                  ▼
                KV `agent-mail`, key m:<agent>:<id>, 30-day TTL
                  ▲
                Worker fetch()  ◀── agent, `Authorization: Bearer <its own token>`
                https://agent-mail.<account subdomain>.workers.dev
```

**The read URL is not written in this repo.** The repo is mirrored to a public GitHub, and the Workers Free plan's 100,000 requests a day are shared across the whole account. A stranger can't read mail, but by flooding a known URL they could use up that allowance and stop delivery until midnight UTC. Agents never give the URL out; it's only where they read their own inbox. Each agent's host keeps it beside the token, as `AGENT_MAIL_URL` next to `AGENT_MAIL_TOKEN`. Operators can find it in the Cloudflare dashboard (Workers & Pages → `agent-mail`) or in the vault's agent-accounts project notes.

The apex `ninjaprivacy.org` stays on Proton. Its MX, SPF and DKIM are untouched. In the Cloudflare dashboard the zone's Email Routing reads **"misconfigured"** because of that. This is expected. **Never "fix records" or enable Email Routing on the apex**, because that would replace the Proton MX and break personal mail.

## What is stored

For each message: envelope sender and recipient, the From address and domain, the `+tag`, Cloudflare's SPF/DKIM/DMARC verdicts, extracted codes and links, the subject and display name, and up to 32 KB of body text (HTML is reduced to text). **Attachments are never stored**, only how many there were. Records expire after `RETENTION_DAYS` (30).

Only the **first** `Authentication-Results` header is trusted, and only when its authserv-id is `mx.cloudflare.net`. Email Routing adds it on top. Any header below it may have been written by the sender.

## API

Every request needs `Authorization: Bearer <token>`. Without a valid token every path returns `401`, so callers can't probe routes. Each token maps to one agent, and every KV key includes that agent, so no request can reach another agent's mail.

| Request | Returns |
|---|---|
| `GET /v1/whoami` | `{ agent }` |
| `GET /v1/messages?since=&from=&tag=&verified=1&limit=` | newest first: `id, receivedAt, from, fromDomain, tag, dmarc, codes` |
| `GET /v1/messages/<id>` | `id, receivedAt, tag, envelopeFrom, from, fromDomain, auth, codes, links, attachments` |
| `GET /v1/messages/<id>?full=1` | the same, plus `untrusted: { notice, fromName, subject, date, text, textTruncated }` |
| `DELETE /v1/messages/<id>` | `204` |

- `since`: ISO time or epoch seconds/ms.
- `from`: a domain (subdomains match) or a full address.
- `verified=1`: only DMARC `pass`. Use it whenever a code's sender matters.
- `limit`: 1-50 (default 20).

**Prompt-injection rule:** by default, responses contain only addresses, verdicts, codes and links. The subject, display name and body text are written by an outside sender. They come back only with `?full=1`, inside an `untrusted` block with a notice. Agent tooling must keep it that way.

## Layout

| Path | What |
|---|---|
| `wrangler.jsonc` | Worker config: KV binding, accepted agents (`AGENTS`), domain, retention |
| `src/inbound.ts` | `email()` handler: check recipient and size, parse with `postal-mime`, store |
| `src/api.ts` | `fetch()` handler: per-agent read API |
| `src/extract.ts` | pure helpers: recipient parsing, auth results, codes, links, HTML→text |
| `src/tokens.ts` | bearer-token check against `AGENT_TOKEN_HASHES` (SHA-256 only) |
| `src/store.ts` | KV key and record shapes |
| `scripts/mint-token.mjs` | make a token without displaying it; prints only its hash |
| `test/` | Vitest in the local Workers runtime (in-memory KV, test-only tokens) |

## Develop and test

Everything below runs locally. Nothing touches Cloudflare.

```bash
npm ci
npm test            # 36 tests, including the hostile-mail test
npm run typecheck   # generates worker-configuration.d.ts (gitignored), then tsc on src and test
```

npm 11 holds back install scripts until approved. `package.json` `allowScripts` approves exactly `esbuild` and `workerd`, which wrangler needs.

## Deploy

Deploys run from an operator workstation with the account-owned token `agent-mail-wrangler`. It has Workers Scripts Edit and Workers KV Storage Edit only, with no DNS or zone rights. It's stored as `CLOUDFLARE_AGENT_MAIL_TOKEN` in that workstation's local secrets file. Read it inside the command so it never shows in the shell history:

```bash
CLOUDFLARE_API_TOKEN="$(sed -n 's/^CLOUDFLARE_AGENT_MAIL_TOKEN=//p' ~/.claude/secrets/api-keys.env)" npx wrangler deploy
```

There is no CI job for this path yet. Merging changes nothing until someone deploys.

### Add an agent

1. Add its name to `AGENTS` in `wrangler.jsonc` (merge request), then deploy.
2. Mint its token **on the host where the agent runs**, into that host's secrets file:
   `node scripts/mint-token.mjs --agent <name> --out <secrets file>`. It prints `{agent, sha256}`. Add `AGENT_MAIL_URL=<read URL>` to the same file. The URL isn't secret, but it stays out of this repo.
3. Add the hash to the secret. The JSON is the whole map, so include every agent's hash:
   `printf '%s' '{"mv-agent":"<hash>","<name>":"<hash>"}' | npx wrangler secret put AGENT_TOKEN_HASHES` (token in env as above).
4. Create the Email Routing rule `<name>@agents.ninjaprivacy.org` → "Send to a Worker" → `agent-mail`. **Catch-all rules are apex-only**, so each agent needs its own literal rule.

### Rotate or revoke a token

Revoke: drop the agent's entry from `AGENT_TOKEN_HASHES` and put the secret again. The old token stops working at once. Rotate: delete the old `AGENT_MAIL_TOKEN=` line on the agent's host, mint again, and put the new hash.
