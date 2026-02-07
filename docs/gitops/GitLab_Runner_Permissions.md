# GitLab Runners & Permissions

## Runner-per-host model
Each host runs its own GitLab Runner (Shell executor).
Deploy jobs execute locally on the target host.

---

## Required Commands
- docker
- install
- sops
- sudo

Optional:
- /usr/local/sbin/gitops-sync (directory sync wrapper)

---

## Sudo Allowlist (recommended)

```
gitlab-runner ALL=(root) NOPASSWD: /usr/bin/docker, /usr/bin/install, /usr/local/sbin/gitops-sync
```

---

## Live Directory Access (ACLs)

Runners need traverse access to live stacks:

```
/home/<svc>/<svc>-hub/compose
```

Grant via ACLs if needed.

---

## Known Gotchas (Solved)

- CRLF in scripts → enforce LF via .gitattributes
- Detached HEAD in CI → expected
- SOPS dotenv requires --output-type dotenv
- Avoid raw sudo rsync → use wrapper
