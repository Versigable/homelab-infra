# Traefik + Cloudflared — 10.0.2.5
**Generated:** 2025-08-26 00:12:54

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
- `config/static/traefik.yml` → `/traefik.yml` (ro)
- `config/dynamic/` → `/etc/traefik/dynamic` (ro)
- `data/acme` → `/etc/traefik/acme`
- `data/cloudflared` → container home (e.g., `/home/nonroot/.cloudflared`)

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

## Quirks
- If tunnel restarts repeatedly, it's almost always perms on `data/cloudflared` (use 65532 ownership).

## Current-State Snapshot (from node)
```
Traefik VM (10.0.2.5)



root@traefik:/home/traefik/traefik-hub# pwd
/home/traefik/traefik-hub
root@traefik:/home/traefik/traefik-hub# ls -R
.:
cloudflared  docker-compose.yml  traefik

./cloudflared:
config.yml  creds

./cloudflared/creds:
37111c6c-064b-4381-9065-9313b50dffb3.json

./traefik:
acme  dynamic  traefik.yml

./traefik/acme:
acme.json

./traefik/dynamic:
authentik.yml  bitwarden.yml  mw-authentik.yml  proxmox.yml  traefik.yml  wikijs.yml
root@traefik:/home/traefik/traefik-hub# cat docker-compose.yml
version: '3.8'

services:
  traefik:
    image: traefik:latest
    container_name: traefik1
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./traefik/dynamic:/etc/traefik/dynamic:ro
      - ./traefik/acme:/etc/traefik/acme
      - ./traefik/traefik.yml:/traefik.yml:ro
    networks:
      - traefik-net1

  cloudflared:
    image: cloudflare/cloudflared:2025.8.1
    container_name: ninjaprivacy_org
    restart: unless-stopped
    command: ["tunnel","--no-autoupdate","--config","/etc/cloudflared/config.yml","run"]
    volumes:
      - ./cloudflared/config.yml:/etc/cloudflared/config.yml:ro
      - ./cloudflared/creds/37111c6c-064b-4381-9065-9313b50dffb3.json:/etc/cloudflared/creds/37111c6c-064b-4381-9065-9313b50dffb3.json:ro
      - ./cloudflared/creds/37111c6c-064b-4381-9065-9313b50dffb3.json:/root/.cloudflared/37111c6c-064b-4381-9065-9313b50dffb3.json:ro
    networks:
      - traefik-net1

networks:
  traefik-net1:
    external: true

volumes:
  traefik-data:
root@traefik:/home/traefik/traefik-hub# cat ./cloudflared/config.yml 
# Use UUID for clarity
tunnel: 37111c6c-064b-4381-9065-9313b50dffb3
credentials-file: /etc/cloudflared/creds/37111c6c-064b-4381-9065-9313b50dffb3.json

ingress:
  - hostname: "*.ninjaprivacy.org"
    service: https://traefik1
    originRequest:
      noTLSVerify: true
  - service: http_status:404
root@traefik:/home/traefik/traefik-hub# cat ./traefik/traefik.yml 
entryPoints:
  web:
    address: :80
    http:
      redirections:
        entryPoint:
          to: websecure
          scheme: https
  websecure:
    address: :443

providers:
  docker:
    exposedByDefault: false
  file:
    directory: /etc/traefik/dynamic
    watch: true

serversTransport:
  insecureSkipVerify: true

log:
  level: ERROR

api:
  dashboard: true
root@traefik:/home/traefik/traefik-hub# cat ./traefik/dynamic/
authentik.yml     bitwarden.yml     mw-authentik.yml  proxmox.yml       traefik.yml       wikijs.yml        
root@traefik:/home/traefik/traefik-hub# cat ./traefik/dynamic/authentik.yml 
http:
  routers:
    authentik-router:
      rule: "Host(`auth.ninjaprivacy.org`)"
      service: authentik-svc
      entrypoints: [websecure]
      tls: {}

    authentik-outpost-router:
      rule: "PathPrefix(`/outpost.goauthentik.io/`)"
      service: authentik-svc
      entrypoints: [websecure]
      tls: {}
      priority: 10000

  services:
    authentik-svc:
      loadbalancer:
        servers:
          - url: https://10.0.1.3:9443
        passHostHeader: true
root@traefik:/home/traefik/traefik-hub# cat ./traefik/dynamic/bitwarden.yml 
http:
  routers:
    bitwarden-router:
      rule: "Host(`bitwarden.ninjaprivacy.org`)"
      service: bitwarden-svc
      entrypoints: [websecure]
      tls: {}
      middlewares:
        - authentik-forward

  services:
    bitwarden-svc:
      loadbalancer:
        servers:
          - url: https://10.0.1.3:1443
        passHostHeader: true
root@traefik:/home/traefik/traefik-hub# cat ./traefik/dynamic/proxmox.yml 
http:
  routers:
    proxmox-router:
      rule: "Host(`proxmox.ninjaprivacy.org`)"
      service: proxmox-svc
      entrypoints: [websecure]
      tls: {}
      middlewares:
        - authentik-forward

  services:
    proxmox-svc:
      loadbalancer:
        servers:
          - url: https://10.0.1.2:8006
        passHostHeader: true
root@traefik:/home/traefik/traefik-hub# cat ./traefik/dynamic/traefik.yml 
http:
  routers:
    traefik-router:
      rule: Host(`traefik.ninjaprivacy.org`) && (PathPrefix(`/api`) || PathPrefix(`/dashboard`))
      service: api@internal
      entrypoints: [websecure]
      middlewares:
        - authentik-forward
      tls: {}

    # Redirect so https://traefik.ninjaprivacy.org reaches the dashboard at https://traefik.ninjaprivacy.org/dashboard/
    traefik-redirect:
      rule: Host(`traefik.ninjaprivacy.org`) && Path(`/`)
      service: dummy
      entrypoints: [websecure]
      middlewares:
        - redirect-to-dashboard
      tls: {}

  # Middleware that modifies the URL to https://traefik.ninjaprivacy.org/dashboard/
  middlewares:
    redirect-to-dashboard:
      redirectRegex:
        regex: "^(https?://[^/]+/?)$"  # Added /? to match optional trailing slash
        replacement: "${1}/dashboard/"
        permanent: false

  services:
    dummy:  # Harmless placeholder; won't be hit even if redirect doesn't work
      loadBalancer:
        servers:
          - url: "http://127.0.0.1"
root@traefik:/home/traefik/traefik-hub# cat ./traefik/dynamic/
authentik.yml     bitwarden.yml     mw-authentik.yml  proxmox.yml       traefik.yml       wikijs.yml        
root@traefik:/home/traefik/traefik-hub# cat ./traefik/dynamic/wikijs.yml 
http:
  routers:
    wikijs-router:
      rule: "Host(`wiki.ninjaprivacy.org`)"
      service: wikijs-svc
      entrypoints: [websecure]
      tls: {}
      middlewares:
        - authentik-forward

  services:
    wikijs-svc:
      loadbalancer:
        servers:
          - url: http://10.0.1.5:3000
        passHostHeader: true
root@traefik:/home/traefik/traefik-hub# cat ./traefik/dynamic/mw-authentik.yml
http:
  middlewares:
    authentik-forward:
      forwardAuth:
        address: "https://10.0.1.3:9443/outpost.goauthentik.io/auth/traefik"
        trustForwardHeader: true
        authResponseHeaders:
          - X-authentik-username
          - X-authentik-groups
          - X-authentik-email
          - X-authentik-name
          - X-authentik-uid
        tls:
          insecureSkipVerify: true
```
