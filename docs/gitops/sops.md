# SOPS + age for homelab-infra

## Convention
- Encrypted dotenv files end in: `*.env.sops` (or `compose.env.sops`)
- Stored under: `hosts/<host>/secrets/`

Example:
- `hosts/traefik/secrets/compose.env.sops`

## .sops.yaml (creation rules)
The repo’s `.sops.yaml` should match the secrets folders and file naming convention so new files encrypt automatically.

## age key on the target host
Store the private key on each target host running a deploy runner:

- `/etc/sops/age/keys.txt`

Recommended perms:
- `chown root:gitlab-runner /etc/sops/age/keys.txt`
- `chmod 0640 /etc/sops/age/keys.txt`
- `chmod 0750 /etc/sops /etc/sops/age`

Validate as `gitlab-runner` on the target host:

```bash
sudo -u gitlab-runner bash -lc 'export SOPS_AGE_KEY_FILE=/etc/sops/age/keys.txt; sops --version'
```

## Dotenv decryption: the critical detail
Dotenv files are NOT JSON/YAML, so you must tell SOPS how to interpret them.

✅ Correct for dotenv:

```bash
sops -d --input-type dotenv --output-type dotenv hosts/traefik/secrets/compose.env.sops
```

If you omit `--output-type dotenv`, you may see:
- `Error dumping file: error emitting binary store: no binary data found in tree`

## Where plaintext lives
Plaintext `.env` is written only on the target host in the live stack dir, e.g.
- `/home/traefik/traefik-hub/compose/.env`

This file should be `0600` and not committed to Git.
