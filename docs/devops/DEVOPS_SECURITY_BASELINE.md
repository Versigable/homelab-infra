# DevOps Security Baseline (Homelab)

This doc defines the homelab’s **security posture** at the DevOps layer:
users, SSH, permissions, secrets hygiene, container hardening, and “what to do when weird stuff happens.”

It’s opinionated, but tuned to the way you actually operate: multiple collaborators, lots of services, and a desire to stay secure without killing velocity.

---

## 1) Identity + access

### 1.1 Core principles
- unique human accounts
- key-based SSH
- least privilege
- auditable access paths

### 1.2 SSH posture
- prefer key-only auth
- prefer per-user restrictions (Match blocks) when needed
- keep root SSH either disabled or tightly scoped

---

## 2) Permissions (collaboration without compromise)

### 2.1 The pattern
- keep `/home/<svc>` private (stealthy)
- open `/home/<svc>/<svc>-hub` to `devops` group with setgid + default ACLs
- keep secrets as opt-out (root-only or ACL removed)

This is implemented by your helper scripts (see **DEVOPS_COLLAB_PERMS.md** and the `devops-*.sh` tools).

---

## 3) Secrets hygiene (broad rules)

Even outside SOPS/GitOps:
- keep secrets out of YAML
- keep secrets out of shell history
- keep secrets out of logs
- don’t bake secrets into images

### 3.1 Secret file rules
If a secret must exist as a file:
- `0600` (or `0640` if service group must read)
- avoid group-readable secrets unless required
- avoid putting secret files in widely-collaborated directories

---

## 4) Container hardening (what matters most)

### 4.1 Runtime defaults
- `restart: unless-stopped`
- healthchecks
- minimal published ports (prefer reverse proxy patterns)
- avoid `privileged: true` unless absolutely necessary
- mount only what you need (least mounts)

### 4.2 Filesystem hardening
Where possible:
- mount configs read-only
- store state in `data/` only
- avoid writing to container rootfs

---

## 5) Supply chain / image hygiene

- pin image versions for critical services
- document the vendor/source of images
- periodically prune old images
- avoid random community images for high-trust services unless you’ve vetted them

---

## 6) Logging + forensics (lightweight but effective)

### 6.1 What to keep
- auth logs (ssh)
- sudo activity
- docker logs (rotated)
- reverse proxy access logs (when relevant)

### 6.2 Quick “is this host weird?” checks
```bash
# who logged in
last -a | head

# ssh keys
find /home -maxdepth 3 -name authorized_keys -print -exec ls -l {} \;

# sudoers fragments
ls -l /etc/sudoers.d

# listening ports
ss -tulpn | head -n 50
```

---

## 7) Incident response (homelab edition)

If you suspect compromise:
1) **isolate** host (pfSense rule or host firewall)
2) snapshot VM/LXC (preserve evidence)
3) rotate secrets used on that host
4) inspect auth logs + recent changes
5) rebuild from known-good + re-deploy services

Keep it simple: isolate → preserve → rotate → rebuild.

---

## 8) Hardening checklist (minimum viable)

- [ ] key-only SSH
- [ ] unique users (no shared accounts)
- [ ] service users locked (nologin unless needed)
- [ ] restricted sudo allowlists
- [ ] docker log rotation enabled
- [ ] minimal published ports
- [ ] secrets not in YAML
- [ ] permissions/ACL model applied to hubs

---

## 9) “Cool next” upgrades

- hardware-backed SSH keys (FIDO2)
- centralized auth (SSO) for internal apps
- vulnerability scanning for images (optional)
- periodic “access review” using `devops-audit.sh` + group membership checks
