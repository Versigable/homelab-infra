# File Map (Natural language → repo path)

Use this as a quick “what do I call it?” map.

---

## Global

- “Inventory hosts” → `inventory/hosts.yml`
- “Inventory DNS” → `inventory/dns.yml`
- “Runner perms script” → `scripts/runner-perms.sh`
- “Root wrapper” → `scripts/root-wrapper.sh`
- “SOPS env encrypt helper” → `scripts/sops_env_encrypt.sh`
- “DevOps grant” → `scripts/devops-grant.sh`
- “DevOps audit” → `scripts/devops-audit.sh`

Docs:
- “GitOps Model B playbook” → `docs/gitops/GitOps_Model_B_Playbook.md`
- “Runner-per-host doc” → `docs/gitops/GitOps_runner-per-host.md`
- “SOPS homelab infra doc” → `docs/gitops/SOPS_homelab_infra.md`
- “Deploy script standard doc” → `docs/gitops/deploy-script-standard.md`

---

## Hosts

### Authentik
- “Authentik host doc” → `hosts/authentik/Homelab_Authentik.md`
- “Authentik deploy script” → `hosts/authentik/scripts/deploy.sh`
- “Authentik compose” → `hosts/authentik/compose/authentik.yml`
- “Authentik secrets” → `hosts/authentik/secrets/authentik.env.sops`

### GamingHub
- “GamingHub host doc” → `hosts/gaminghub/Homelab_GamingHub.md`
- “Game server admin doc” → `hosts/gaminghub/Game_Server_Admin.md`
- “GamingHub deploy script” → `hosts/gaminghub/scripts/deploy.sh`
- “GamingHub compose <service>” → `hosts/gaminghub/compose/<service>.yml`
- “GamingHub secrets <service>” → `hosts/gaminghub/secrets/<service>.env.sops`

Services (compose):
- Ark Island → `hosts/gaminghub/compose/ark-island.yml`
- Ark Ragnarok → `hosts/gaminghub/compose/ark-ragnarok.yml`
- Ark Fjordur → `hosts/gaminghub/compose/ark-fjordur.yml`
- Astroneer → `hosts/gaminghub/compose/astroneer.yml`
- Minecraft → `hosts/gaminghub/compose/minecraft.yml`
- Palworld → `hosts/gaminghub/compose/palworld.yml`
- Satisfactory → `hosts/gaminghub/compose/satisfactory.yml`
- SotF → `hosts/gaminghub/compose/sotf.yml`
- Valheim → `hosts/gaminghub/compose/valheim.yml`

### ServiceHub
- “ServiceHub host doc” → `hosts/servicehub/Homelab_ServiceHub.md`
- “ServiceHub deploy script” → `hosts/servicehub/scripts/deploy.sh`
- “Wiki compose” → `hosts/servicehub/compose/wiki.yml`
- “n8n compose” → `hosts/servicehub/compose/n8n.yml`
- “Wiki secrets” → `hosts/servicehub/secrets/wiki.env.sops`
- “n8n secrets” → `hosts/servicehub/secrets/n8n.env.sops`

### Traefik
- “Traefik host doc” → `hosts/traefik/Homelab_Traefik.md`
- “Traefik README” → `hosts/traefik/README.md`
- “Traefik deploy script” → `hosts/traefik/scripts/deploy.sh`
- “Traefik compose” → `hosts/traefik/compose/docker-compose.yml`
- “Traefik compose secrets” → `hosts/traefik/secrets/compose.env.sops`
- “Traefik static config” → `hosts/traefik/config/static/traefik.yml`
- “Traefik dynamic <name>” → `hosts/traefik/config/dynamic/<name>.yml`

Dynamic names (common):
- mw-authentik → `hosts/traefik/config/dynamic/mw-authentik.yml`
- traefik → `hosts/traefik/config/dynamic/traefik.yml`
- authentik → `hosts/traefik/config/dynamic/authentik.yml`
- proxmox → `hosts/traefik/config/dynamic/proxmox.yml`
- truenas → `hosts/traefik/config/dynamic/truenas.yml`
- games-tcp → `hosts/traefik/config/dynamic/games-tcp.yml`
- games-udp → `hosts/traefik/config/dynamic/games-udp.yml`
- ue5-udp → `hosts/traefik/config/dynamic/ue5-udp.yml`
- gitlab → `hosts/traefik/config/dynamic/gitlab.yml`
- gitlab-registry → `hosts/traefik/config/dynamic/gitlab-registry.yml`

### GitLab (host folder)
- “GitLab host stuff” → `hosts/gitlab/...` (currently empty; fill in as you add files)

---

## Naming shortcut suggestions (optional)

If you like, we can adopt a shorthand:
- `traefik:static` → Traefik static config
- `traefik:dyn:mw-authentik` → mw-authentik dynamic config
- `servicehub:deploy` → ServiceHub deploy.sh
- `gaminghub:compose:astroneer` → astroneer compose
- `authentik:secrets` → authentik.env.sops
