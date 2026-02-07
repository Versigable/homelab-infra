# DevOps Collaboration Permissions (Homelab)

This guide formalizes how we enable **safe, repeatable collaboration** on service repos under:

```
/home/<service_user>/<service>-hub
```

Examples: `/home/traefik/traefik-hub`, `/home/authentik/authentik-hub`, `/home/wiki/wiki-hub`.

We grant **write** for a shared group `devops` **only inside the repo**, keep the parent home folder private, and ensure **new files/dirs remain group‑writable** via `setgid` and default ACLs.

---

## Security model (TL;DR)
- Parent home (e.g., `/home/traefik`) is **stealthy**: only owner + root can list; `devops` can traverse but not list.
- Repo dir (e.g., `/home/traefik/traefik-hub`) is **collab‑friendly**:
  - owner: `<service_user>`
  - group: `devops`
  - mode: directories **2775** (setgid), files **0664**
  - ACLs: `group:devops:rwX` **now and by default**
- Users are added to `devops` to collaborate. Outside the repo they remain unprivileged.
- For private secrets (e.g., `.env`), you can opt out of `devops` access per‑file.

---

## Prereqs
- Linux host(s) running Debian/Ubuntu (uses `setfacl`, `loginctl`).
- SSH access as a privileged user (root or sudo).
- Standardized path shape `/home/<service_user>/<service>-hub`.

---

## One‑shot usage

> **Recommended:** Use the script below for each repo/host.

```
# as root (or sudo -E bash ...)
/usr/local/sbin/devops-grant.sh /home/<service_user>/<service>-hub /home/<service_user>
```

The script is **idempotent**. Re‑running it is safe.

### After adding a user to the group
A user needs a **new login** to pick up `devops` membership.

```
# as root on the host
loginctl terminate-user <username> 2>/dev/null || pkill -KILL -u <username>

# then reconnect (e.g., VS Code Remote-SSH)
```

---

## Install the helper scripts

Copy these to the host (or paste the contents) and make executable:

```
install -m 0755 devops-grant.sh  /usr/local/sbin/devops-grant.sh
install -m 0755 devops-revoke.sh /usr/local/sbin/devops-revoke.sh
install -m 0755 devops-audit.sh  /usr/local/sbin/devops-audit.sh
```

Scripts are included in `scripts/` of this package.

---

## Add / remove users

```
# add an existing user to devops
usermod -aG devops <username>
# remove user from devops (will drop access immediately on new login)
gpasswd -d <username> devops

# list the group
getent group devops
```

> After adding/removing, force a fresh login as shown above.

---

## Example (Traefik)

```
devops-grant.sh /home/traefik/traefik-hub /home/traefik
```

Then reconnect as your unprivileged user and verify:

```
id                                   # should include devops
touch /home/traefik/traefik-hub/.t   # create
rm    /home/traefik/traefik-hub/.t   # delete
```

---

## Keeping secrets private

To **exclude** a sensitive file from devops (e.g., `.env`):

```
chmod 0640 /home/<service_user>/<service>-hub/compose/.env
setfacl -x g:devops /home/<service_user>/<service>-hub/compose/.env
```

---

## Troubleshooting

**User can’t write even after being added to devops**  
→ They’re still in an old session. Terminate sessions and reconnect:
```
loginctl terminate-user <username> 2>/dev/null || pkill -KILL -u <username>
```

**VS Code asks for password or can’t save**  
→ Ensure repo has setgid + default ACLs and group is `devops`:
```
namei -l /home/<service_user>/<service>-hub
getfacl -p /home/<service_user>/<service>-hub | sed -n '1,40p'
```

**Cannot ls parent `/home/<service_user>`**  
→ Expected. Parent is stealthy (`750`) with only traverse `x` for devops via ACL.

---

## What the script does (exactly)

1. Ensures `devops` group exists.
2. **Parent home**: `chmod 750 /home/<service_user>` and `setfacl -m g:devops:x` (traverse only).
3. **Repo dir**:
   - `chgrp -R devops` (group devops)
   - directories `chmod 2775` (setgid keeps group for new subdirs)
   - files `chmod 0664`
   - ACLs: `setfacl -R -m g:devops:rwX` and default `setfacl -R -m d:g:devops:rwX`

This yields collaboration without granting sudo or shell upgrades.

---

## Audit

```
devops-audit.sh /home/<service_user>/<service>-hub
```

Shows ownership, key modes, and ACL highlights.

---

## Change log
- v1.0 • Initial formalization (Traefik, ServiceHub, Authentik validated).

