# Model B GitOps Playbook (homelab-infra)

## Goal
Git is the **single source of truth** for each host’s Compose, config, and secrets (encrypted).
Every deploy run performs a full **repo → live sync**, decrypts secrets **on-host**, then
runs `docker compose up -d`.

---

## Mental Model

- **GitLab** stores repo + CI pipelines
- **Runner-per-host (Shell executor)** executes deploys *on the target host*
- **Repo** holds desired state
- **Host** holds runtime state only

---

## Repo Layout (canonical)

```
hosts/
  traefik/
    compose/docker-compose.yml
    config/{static,dynamic}/*.yml
    secrets/compose.env.sops
    scripts/deploy.sh

  servicehub/
    compose/wiki.yml
    compose/n8n.yml
    secrets/*.env.sops
    scripts/deploy.sh

  gaminghub/
    compose/*.yml
    secrets/*.env.sops
    scripts/deploy.sh

  authentik/
    compose/authentik.yml
    secrets/authentik.env.sops
    scripts/deploy.sh
```

---

## Deploy Script Contract (Model B)

Every deploy script must:

1. Resolve repo root (CI + local safe)
2. Sync repo → live (compose/config)
3. Decrypt secrets via SOPS + age
4. Install `.env` as `root:root 0600`
5. Run `docker compose pull && up -d`

---

## Outcomes (Verified)

- Traefik: compose + static/dynamic config
- ServiceHub: wiki + n8n
- GamingHub: multi-game stacks
- Authentik: compose + secrets

All pipelines green. Live files match Git.
