# Traefik + Cloudflared — 10.0.2.5
**Updated:** 2026-03-30

## Overview
- **Project path:** `/home/traefik/traefik-hub/`
- **Compose:** `compose/docker-compose.yml` (run from `compose/`)
- **Role:** Edge proxy for all subdomains via Cloudflare Tunnel
- **Status:** `traefik1` Up, cloudflared Up
- **Standard layout:**
```
/home/<svc>/<proj>-hub/
├─ compose/                 # docker-compose.yml + .env (600)
├─ config/
│  ├─ static/               # static config (e.g., traefik.yml)
│  └─ dynamic/              # dynamic YAMLs (routers/middleware)
├─ data/                    # app state (db, media, acme, cloudflared, …)
├─ logs/
├─ backups/
└─ (legacy/ kept until verified → then pruned)
```

## Mounts (expected)
- `config/static/traefik.yml` → `/etc/traefik/traefik.yml` (ro)
- `config/dynamic/` → `/etc/traefik/dynamic` (ro)
- `data/acme` → `/etc/traefik/acme`
- `data/cloudflared/config.yml` → `/etc/cloudflared/config.yml` (ro)
- `data/cloudflared/creds/` → `/etc/cloudflared/creds/` + `/root/.cloudflared/` (ro)

## Ownership & Permissions
- Tree owned by **traefik:traefik**.
- `compose/.env` → **600**.
- **Cloudflared:** must be owned by **65532:65532**; dirs **750**; files **640**.
  ```bash
  chown -R 65532:65532 data/cloudflared
  chmod 750 data/cloudflared data/cloudflared/creds
  chmod 640 data/cloudflared/config.yml data/cloudflared/creds/*.json
  ```
- **ACME:** Traefik creates its storage files itself (mode 600); don't pre-create
  them, because the setgid/default-ACL dir would make them 660/664, which Traefik
  refuses. `data/acme/acme.json` is a stale 2025 leftover (664) that nothing uses.

## TLS (origin certificate)
One Let's Encrypt wildcard, `*.ninjaprivacy.org`, via Cloudflare DNS-01:
- `config/static/traefik.yml` defines resolver `letsencrypt` (lego `cloudflare`
  provider, reads `CF_DNS_API_TOKEN` from `compose/.env`, which deploy.sh decrypts
  from `secrets/compose.env.sops`; the token needs Zone:Read + DNS:Edit).
- `config/dynamic/tls.yml` orders the wildcard once through
  `tls.stores.default.defaultGeneratedCert`. Traefik picks it by SNI for every router.
- **Routers use `tls: {}` and never name `certResolver`.** A router with
  `certResolver` orders its own per-host cert, which publishes that hostname in
  public Certificate Transparency logs and gets renewed forever.
- Rollout (2026-10-12): staging first (MR !133: `caServer` = LE staging, storage
  `acme-staging.json`, issued OK), then production (no `caServer`, storage
  `acme-prod.json`). `acme-staging.json` and the old `acme.json` are unused leftovers;
  `acme.json` holds 2025 private keys at mode 664, so move it somewhere root-only.
- Check issuance on the VM (it runs in the background; log.level ERROR hides success):
  `openssl s_client -connect 127.0.0.1:443 -servername probe.ninjaprivacy.org </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -enddate`
  and `sudo docker logs --since 15m traefik1 2>&1 | grep -iE 'acme|resolver|certificate'`.
  A failed first order is retried only after `sudo docker restart traefik1`.
- cloudflared still uses `noTLSVerify` towards `https://traefik1`, so the public
  path never depended on this cert. Direct access (LAN, Tailscale route to
  10.0.2.5) does.
- Any change to `config/static/traefik.yml` (outside comments/blank lines) now
  recreates traefik1 on deploy (deploy.sh hashes it into the env), dropping all
  proxied sessions for a few seconds; batch static edits.

## Routing Notes
- **CouchDB (`brain.ninjaprivacy.org`)** uses a split-router pattern:
  - `couchdb-api-router` (priority 1) — open, no auth middleware. Exposes the CouchDB HTTP API directly.
  - `couchdb-fauxton-router` (priority 100) — matches `/_utils` prefix, gated behind `authentik-forward`. Protects the Fauxton admin UI.
- All other HTTP services use a single router with `authentik-forward` middleware (except the Authentik outpost itself).

## Quirks
- If tunnel restarts repeatedly, it's almost always perms on `data/cloudflared` (use 65532 ownership).

## Dynamic Configs (current)
| File | Hostname / Purpose | Backend | Auth |
|---|---|---|---|
| `authentik.yml` | `auth.ninjaprivacy.org` | `10.0.1.3:9443` | none (is the auth provider) |
| `bitwarden.yml` | `bitwarden.ninjaprivacy.org` | `10.0.1.3:1443` | authentik |
| `couchdb.yml` | `brain.ninjaprivacy.org` | `10.0.1.5:5984` | API open / Fauxton (`/_utils`) behind authentik |
| `gitlab.yml` | `gitlab.ninjaprivacy.org` | — | authentik |
| `gitlab-registry.yml` | `registry.ninjaprivacy.org` | — | authentik |
| `n8n.yml` | `n8n.ninjaprivacy.org` | — | authentik |
| `proxmox.yml` | `proxmox.ninjaprivacy.org` | `10.0.1.2:8006` | authentik |
| `traefik.yml` | `traefik.ninjaprivacy.org` | `api@internal` | authentik |
| `truenas.yml` | `truenas.ninjaprivacy.org` | — | authentik |
| `wikijs.yml` | `wiki.ninjaprivacy.org` | `10.0.1.5:3000` | authentik |
| `mw-authentik.yml` | ForwardAuth middleware definition | `10.0.1.3:9443` | — |
| `games-tcp.yml` | TCP game entrypoints (Satisfactory) | — | — |
| `games-udp.yml` | UDP game entrypoints (Ark, Valheim, etc.) | — | — |
| `ue5-udp.yml` | UE5 Concert UDP | — | — |
