# Runner-per-Host GitOps Deployment Pattern

This document defines the **runner-per-host deployment model** used in `homelab-infra`,
including CI routing, permissions, filesystem expectations, and common pitfalls.

This is a **Model B GitOps** pattern:
> Git defines desired state, and each deploy run enforces repo → live sync.

---

## Why runner-per-host?

A deploy job **must run on the host being deployed** so it can:

- decrypt secrets **locally** (SOPS + age private keys never leave the host)
- write config and compose files to **real local filesystem paths**
- restart **only the local Docker stack**
- avoid SSH fan-out, agent forwarding, or remote execution complexity

This keeps the blast radius small and the mental model simple.

---

## High-level architecture

```
GitLab repo (homelab-infra)
        |
        |  CI pipeline
        v
Runner (shell executor, per host)
        |
        |  deploy.sh
        v
Local host filesystem + Docker
```

Each host runs **its own GitLab Runner**, registered with a **unique tag**.

---

## CI job routing (runner tags)

Each runner is tagged for the host it lives on.

Examples:
- Traefik host → runner tag: `traefik`
- Authentik host → runner tag: `authentik`
- GamingHub host → runner tag: `gaminghub`
- ServiceHub host → runner tag: `servicehub`

`.gitlab-ci.yml` routes jobs like this:

```yaml
deploy-traefik:
  stage: deploy
  tags: ["traefik"]
  rules:
    - if: '$CI_COMMIT_BRANCH == "main"'
      changes:
        - hosts/traefik/**/*
  script:
    - bash hosts/traefik/scripts/deploy.sh
```

This **guarantees** the job runs on the correct machine.

---

## Shell executor expectations

With the Shell executor:

- the repo is checked out to a path like:
  ```
  /home/gitlab-runner/builds/<runner-id>/<project-path>/
  ```
- CI jobs typically run in a **detached HEAD**
- deploy scripts **must not assume** they are in the live stack directory

### Repo root resolution (required)

Every deploy script should locate the repo root safely:

```bash
REPO_DIR="${CI_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
```

This works in:
- GitLab CI
- local testing
- SSH sessions

---

## Repo vs Live filesystem layout

The repo is **not** the runtime environment.

Example (Traefik):

```
Repo:
  hosts/traefik/compose/docker-compose.yml
  hosts/traefik/secrets/compose.env.sops

Live host:
  /home/traefik/traefik-hub/compose/docker-compose.yml
  /home/traefik/traefik-hub/compose/.env
```

Deploy scripts explicitly bridge this gap.

---

## Deploy script contract (Model B)

Every deploy script **must** do the following, in order:

1. Resolve repo root
2. Sync compose and config from repo → live
3. Decrypt secrets on-host
4. Install `.env` as `root:root 0600`
5. Run `docker compose pull && docker compose up -d`
6. Show status with `docker compose ps`

If it’s not in Git, it does not exist.

---

## Required commands on runner hosts

Minimum required:

- `docker`
- `install`
- `sops`
- `sudo`

Recommended:

- `/usr/local/sbin/gitops-sync` (safe directory sync wrapper)

---

## Sudo allowlist (least privilege)

Create a dedicated sudoers file:

```
/etc/sudoers.d/gitlab-runner-gitops
```

```text
gitlab-runner ALL=(root) NOPASSWD:   /usr/bin/docker,   /usr/bin/install,   /usr/local/sbin/gitops-sync
```

Validate with:

```bash
sudo visudo -cf /etc/sudoers.d/gitlab-runner-gitops
```

---

## Live directory access (ACLs)

Runners need **traverse** access to live stacks, but not ownership.

Example live path:
```
/home/<svc>/<svc>-hub/compose
```

Grant ACLs if needed:

```bash
sudo setfacl -m u:gitlab-runner:rx /home/<svc>
sudo setfacl -m u:gitlab-runner:rx /home/<svc>/<svc>-hub
sudo setfacl -m u:gitlab-runner:rx /home/<svc>/<svc>-hub/compose
```

---

## Secrets handling (summary)

- Encrypted secrets live in Git as `*.env.sops`
- Private age keys live **only on the host**
- Plaintext `.env` exists **only at runtime**

Correct decrypt command:

```bash
sops -d --input-type dotenv --output-type dotenv file.env.sops
```

Install with:

```bash
install -m 0600 -o root -g root .env DEST
```

---

## Known gotchas (solved)

- **CRLF in scripts** → enforce LF via `.gitattributes`
- **Detached HEAD in CI** → expected behavior
- **SOPS dotenv errors** → always specify `--output-type dotenv`
- **sudo rsync failures** → use the wrapper instead

---

## Verified state (2026-02)

This model is confirmed working across:

- Traefik (compose + static + dynamic config)
- ServiceHub (wiki, n8n)
- GamingHub (multi-game stacks)
- Authentik (compose + secrets)

All deploy jobs are green.  
Live state matches Git.

---

## Philosophy

> Git defines reality.  
> CI enforces reality.  
> Hosts never drift.
