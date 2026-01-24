# Runner-per-host deployment pattern

## Why runner-per-host?
A deploy job should run *on the host being deployed* so it can:
- decrypt secrets locally
- write config to the correct local filesystem paths
- restart only the local stack

## Tags (routing deploy jobs)
Each host runner gets a unique tag. Example:

- Traefik runner tags: `traefik`

Then `.gitlab-ci.yml` uses:

```yaml
tags: ["traefik"]
```

so the job is guaranteed to run on that host’s runner.

## Shell executor expectations
With the Shell executor:
- the repo is checked out into a runner working directory like:
  `/home/gitlab-runner/builds/<runner-id>/<project-path>/`
- your deploy script must assume it’s running inside that checked-out repo
- use `REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"` to locate repo root reliably

## Host path access (Traefik example)
Your live stack is **not** in the runner checkout. It lives here:

- Live stack dir: `/home/traefik/traefik-hub/compose`
- Secrets are in repo: `hosts/traefik/secrets/compose.env.sops`

So deploy must:
1) decrypt from repo
2) write `.env` into live stack
3) `docker compose up -d` in the live stack directory
