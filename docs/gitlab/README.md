# GitLab + GitOps (homelab-infra)

This repo is the single source of truth for homelab deployments (Compose today, Kubernetes/Argo later).

## Mental model

- **GitLab (the app):** hosts repos + CI pipelines
- **GitLab Runner (per host):** executes deploy jobs *on the target host*
- **SOPS + age:** secrets are encrypted in Git; decrypted only on the target host at deploy time
- **Deploy script (per host):** does the actual update on the target host (writes `.env`, runs compose, etc.)

## Repo layout (current)

```
homelab-infra/
  .gitlab-ci.yml
  .sops.yaml
  hosts/
    traefik/
      compose/            # (optional) compose files you want GitOps-managed
      config/             # (optional) traefik dynamic/static snippets you want GitOps-managed
      secrets/
        compose.env.sops  # SOPS-encrypted dotenv for Traefik stack
      scripts/
        deploy.sh         # deploy script executed by Traefik runner
```

## Where commands are run (IMPORTANT)

### On your workstation (dev box)
- Edit repo files
- `git commit` / `git push`
- Create Merge Requests (optional)

### On GitLab host (the GitLab LXC/VM)
- GitLab admin tasks (create project, register runners, set protected branches, etc.)
- You usually do NOT run deploy commands here

### On Traefik host (target machine + runner machine)
- GitLab Runner service runs here
- Pipeline job executes here under the `gitlab-runner` user
- Decryption happens here using `/etc/sops/age/keys.txt`
- Actual compose deploy happens here against `/home/traefik/traefik-hub/compose`

## Security posture
- Encrypted secrets are stored in Git (`*.env.sops`)
- Plaintext `.env` exists only on the target host in the live stack directory
- Runner has only the minimal perms needed:
  - read age key
  - write `.env` into the live stack
  - run docker compose (typically via docker group or sudo allowlist)
