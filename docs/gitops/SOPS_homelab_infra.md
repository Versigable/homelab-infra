# SOPS + age Secrets Management (homelab-infra)

> Canonical, battle-tested pattern for **GitLab CI runner-per-host** deploys: secrets encrypted in Git, decrypted only on the target host at deploy time.

---

## Mental model

- **Git** stores only encrypted secrets (`*.env.sops`).
- **Each host** has its own **age private key** (stored locally).
- **Deploy jobs run on the target host** (runner-per-host).
- Deploy script **decrypts** the host’s `hosts/<host>/secrets/*.env.sops` → writes plaintext `.env` into the **live stack dir** → runs `docker compose up -d`.
- Plaintext `.env` **never** lives in the repo and **never** leaves the host.

---

## Current repo convention (matches `.sops.yaml`)

### Encrypted dotenv naming + location

Your current `.sops.yaml` uses this pattern per host:

- **Path:** `hosts/<host>/secrets/`
- **Filename:** `*.env.sops` (any name, must end with `.env.sops`)

Examples:
- `hosts/traefik/secrets/compose.env.sops`
- `hosts/servicehub/secrets/wiki.env.sops`
- `hosts/servicehub/secrets/n8n.env.sops`
- `hosts/authentik/secrets/authentik.env.sops`
- `hosts/gaminghub/secrets/minecraft.env.sops`

### Creation rules (as of now)

> These rules determine which **age recipient(s)** encrypt each file based on its **output path**.

```yaml
creation_rules:
  # Traefik host (10.0.2.5)
  - path_regex: ^hosts/traefik/secrets/.*\.env\.sops$
    age: ["<traefik_age_pub>"]

  # ServiceHub host (10.0.1.5)
  - path_regex: '(^|.*/)hosts/servicehub/secrets/.*\.env\.sops$'
    age: ["<servicehub_age_pub>"]

  # Authentik host (10.0.1.3)
  - path_regex: ^hosts/authentik/secrets/.*\.env\.sops$
    age: ["<authentik_age_pub>"]

  # GamingHub host (10.0.1.9)
  - path_regex: ^hosts/gaminghub/secrets/.*\.env\.sops$
    age: ["<gaminghub_age_pub>"]

  # Future: Kubernetes / Argo secrets
  - path_regex: ^k8s/.*/secrets/.*\.(yaml|yml|json)\.sops$
    age: ["<traefik_age_pub>"]

  - path_regex: ^clusters/.*/secrets/.*\.(yaml|yml|json)\.sops$
    age: ["<traefik_age_pub>"]
```

**Notes on the regexes**
- The ServiceHub rule uses `(^|.*/)hosts/servicehub/...` which matches both:
  - `hosts/servicehub/secrets/...`
  - `./hosts/servicehub/secrets/...` (and similar “prefixed” paths)
- The others anchor with `^hosts/...` (repo-root relative). That’s totally fine, just be consistent in how you write files (see next section).

---

## Host key location + permissions (target host)

### Canonical key path

```
/etc/sops/age/keys.txt
```

### Recommended perms

```bash
sudo chown root:gitlab-runner /etc/sops/age/keys.txt
sudo chmod 0640 /etc/sops/age/keys.txt
sudo chmod 0750 /etc/sops /etc/sops/age
```

### Validate as `gitlab-runner`

```bash
sudo -u gitlab-runner bash -lc 'export SOPS_AGE_KEY_FILE=/etc/sops/age/keys.txt; sops --version'
```

If this fails, deploys on that host will fail.

---

## Dotenv decrypt (the critical detail)

Dotenv files are **not** YAML/JSON, so SOPS must be told how to interpret them.

✅ Correct:

```bash
sops -d --input-type dotenv --output-type dotenv hosts/<host>/secrets/<name>.env.sops
```

If you omit `--output-type dotenv`, you may see:

```
Error dumping file: error emitting binary store: no binary data found in tree
```

---

## Creating / editing secrets safely (creation_rules-friendly)

### Preferred (edit in-place with `sops`)

This is the cleanest way to ensure creation rules match the **output file path**:

```bash
# create new (uses creation_rules by output path)
sops hosts/<host>/secrets/<name>.env.sops

# edit existing
sops hosts/<host>/secrets/<name>.env.sops
```

### If you must create from a plaintext `.env`

Avoid redirecting stdout (`>`), because SOPS may not apply the right creation rule without a real output path.

✅ Do this:

```bash
sops --input-type dotenv --output-type dotenv   --encrypt   --output hosts/<host>/secrets/<name>.env.sops   /path/to/plain.env
```

If you have to generate content programmatically, still write via `--output` / `-o` to the final `*.env.sops` path.

---

## Safe deploy pattern (required)

Use a **subshell + temp file + EXIT trap** so secrets do not linger if the job fails mid-flight.

```bash
(
  umask 077
  TMP_ENV="$(mktemp)"
  trap 'rm -f "${TMP_ENV}"' EXIT

  sops -d --input-type dotenv --output-type dotenv "$SOPS_ENV" > "$TMP_ENV"
  sudo install -m 0600 -o root -g root "$TMP_ENV" "$LIVE_ENV"
)
```

Why this matters:
- guarantees `0600` perms
- prevents partial writes
- ensures cleanup on failure

---

## Where plaintext lives

Plaintext `.env` exists only on the **target host** in the **live stack directory** (never in Git).

Examples:
- `/home/traefik/traefik-hub/compose/.env`
- `/home/authentik/authentik-hub/compose/.env`
- `/home/wiki/wiki-hub/compose/.env` (ServiceHub service-based layout)

**Required perms:** `0600 root:root`

---

## GitLab CI integration (runner-per-host)

### CI job rule of thumb

- Use runner tags to guarantee the job runs on the correct host.
- Use `changes:` so only relevant host changes trigger deploy.

Example:

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

---

## Recommended operational guardrails

- **Never print** decrypted env (avoid `set -x`; always `set +x` before decrypt).
- Keep `/etc/sops/age/keys.txt` readable only by `root` + `gitlab-runner` group.
- Keep plaintext `.env` **root-only** (`0600 root:root`).
- Consider a pre-commit / CI check to block committing `compose/.env` accidentally.
- Consider moving future `k8s/` and `clusters/` recipients to **their own keys** (right now they use the Traefik key by design).

---

## Quick checklist (new host)

1) Generate age keypair on the host and store private key at:
   - `/etc/sops/age/keys.txt`

2) Set perms:
   - `root:gitlab-runner 0640`, dirs `0750`

3) Add host’s **public** key to `.sops.yaml` under a creation rule matching:
   - `hosts/<host>/secrets/.*\.env\.sops$`

4) Create `hosts/<host>/secrets/<name>.env.sops` using `sops <path>`

5) Ensure deploy script uses:
   - `sops -d --input-type dotenv --output-type dotenv`
   - temp file + trap pattern
   - `install -m 0600 -o root -g root`

---

*Last updated: 2026-02-07*
