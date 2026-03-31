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
- **ACME:** `data/acme/acme.json` → **600**.

## TLS (Origin) Canary Plan (wiki only)
1) Add to static config (`config/static/traefik.yml`):
```yaml
certificatesResolvers:
  cf-dns:
    acme:
      email: "Paris-chose-venus@pm.me"
      storage: /etc/traefik/acme/acme.json
      dnsChallenge:
        provider: cloudflare
```
2) Put `CF_DNS_API_TOKEN=...` in `compose/.env` (zone-scope: DNS:Edit).
3) In wiki router (dynamic), set `tls.certResolver: cf-dns`.
4) In cloudflared `data/cloudflared/config.yml`, for wiki:
```yaml
ingress:
  - hostname: wiki.ninjaprivacy.org
    service: https://traefik:443
    originRequest:
      originServerName: wiki.ninjaprivacy.org
```
5) Apply: `docker compose up -d traefik && docker compose restart cloudflared`.
**Rollback:** revert wiki to `http://traefik:80` and remove the `tls:` block.

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
