2026-01-20 03:21:09 UTC

## who.ninjaprivacy.org (admin-only "who's on")

`config/who/who.py` runs in the `who` container (compose), reached only by Traefik on
`traefik-net1`. It's behind `authentik-forward` (`config/dynamic/who.yml`) and also
requires the `Hokagis` group. When someone opens the page, it reads:

- Traefik's minimal access log, `data/traefik-logs/` (time, host, router and Authentik
  username only). Only routers using `authentik-forward` are trusted.
- Authentik's event log, through the read-only `lab-activity` service account.
- Jellyfin sessions and last activity.
- Navidrome user last-access times. Never the library or what's playing.
- Game-server player names.

It caches the result for 60 s and stores nothing. Secrets are in `secrets/who.env.sops`,
installed as `compose/who.env`: kept out of `.env` so they never recreate traefik1. Bitwarden:
`[token] Authentik · lab-activity`, `[token] Jellyfin · lab-activity`, `[app] Navidrome · lab-activity`.
Vault: 1-Projects/lab-activity-monitoring.
