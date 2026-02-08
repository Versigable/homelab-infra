# Raw Links Index (homelab-infra)

Purpose: central place to click raw GitHub URLs so we can quickly fetch exact file contents without hunting through GitHub UI.

> Tip: Prefer the `.../main/...` raw format below (clean + stable).
> Backup format: `.../refs/heads/main/...` also works.

---

## Top-level

- README  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/README.md
- RAW_LINKS.MD in GitHub mirror
  https://github.com/Versigable/homelab-infra/blob/main/docs/_index/raw_links.md 

---

## Docs

### DevOps
- DEVOPS_COLLAB_PERMS.md  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/docs/devops/DEVOPS_COLLAB_PERMS.md
- DEVOPS_HOST_BASELINE.md  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/docs/devops/DEVOPS_HOST_BASELINE.md
- DEVOPS_OPERATIONS_RUNBOOK.md  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/docs/devops/DEVOPS_OPERATIONS_RUNBOOK.md
- DEVOPS_SECURITY_BASELINE.md  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/docs/devops/DEVOPS_SECURITY_BASELINE.md
- DEVOPS_SERVICE_STANDARDS.md  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/docs/devops/DEVOPS_SERVICE_STANDARDS.md

### GitLab
- ci-template.md  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/docs/gitlab/ci-template.md

### GitOps
- GitOps_Model_B_Playbook.md  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/docs/gitops/GitOps_Model_B_Playbook.md
- GitOps_runner-per-host.md  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/docs/gitops/GitOps_runner-per-host.md
- SOPS_homelab_infra.md  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/docs/gitops/SOPS_homelab_infra.md
- deploy-script-standard.md  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/docs/gitops/deploy-script-standard.md

---

## Inventory

- hosts.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/inventory/hosts.yml
- dns.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/inventory/dns.yml

---

## Shared scripts

- GITOPS_RUNNER_SOPS_HELPERS.md  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/scripts/GITOPS_RUNNER_SOPS_HELPERS.md
- devops-audit.sh  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/scripts/devops-audit.sh
- devops-grant.sh  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/scripts/devops-grant.sh
- devops-revoke.sh  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/scripts/devops-revoke.sh
- devops-add-member.sh  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/scripts/devops-add-member.sh
- devops-remove-member.sh  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/scripts/devops-remove-member.sh
- devops-user-keyonly.sh  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/scripts/devops-user-keyonly.sh
- devops-batch-apply.py  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/scripts/devops-batch-apply.py
- runner-perms.sh  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/scripts/runner-perms.sh
- root-wrapper.sh  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/scripts/root-wrapper.sh
- sops_env_encrypt.sh  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/scripts/sops_env_encrypt.sh

---

## Hosts

### Authentik
- Homelab_Authentik.md  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/authentik/Homelab_Authentik.md
- deploy.sh  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/authentik/scripts/deploy.sh
- test.sh  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/authentik/scripts/test.sh
- compose/authentik.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/authentik/compose/authentik.yml
- secrets/authentik.env.sops  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/authentik/secrets/authentik.env.sops

### GamingHub
- Homelab_GamingHub.md  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/Homelab_GamingHub.md
- Game_Server_Admin.md  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/Game_Server_Admin.md
- deploy.sh  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/scripts/deploy.sh
- test.sh  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/scripts/test.sh

Compose:
- ark-island.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/compose/ark-island.yml
- ark-ragnarok.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/compose/ark-ragnarok.yml
- ark-fjordur.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/compose/ark-fjordur.yml
- astroneer.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/compose/astroneer.yml
- minecraft.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/compose/minecraft.yml
- palworld.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/compose/palworld.yml
- satisfactory.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/compose/satisfactory.yml
- sotf.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/compose/sotf.yml
- valheim.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/compose/valheim.yml

Secrets:
- ark-island.env.sops  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/secrets/ark-island.env.sops
- ark-ragnarok.env.sops  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/secrets/ark-ragnarok.env.sops
- ark-fjordur.env.sops  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/secrets/ark-fjordur.env.sops
- astroneer.env.sops  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/secrets/astroneer.env.sops
- minecraft.env.sops  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/secrets/minecraft.env.sops
- palworld.env.sops  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/secrets/palworld.env.sops
- satisfactory.env.sops  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/secrets/satisfactory.env.sops
- sotf.env.sops  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/secrets/sotf.env.sops
- valheim.env.sops  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/gaminghub/secrets/valheim.env.sops

### ServiceHub
- Homelab_ServiceHub.md  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/servicehub/Homelab_ServiceHub.md
- deploy.sh  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/servicehub/scripts/deploy.sh
- balls.sh  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/servicehub/scripts/balls.sh
- compose/wiki.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/servicehub/compose/wiki.yml
- compose/n8n.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/servicehub/compose/n8n.yml
- secrets/wiki.env.sops  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/servicehub/secrets/wiki.env.sops
- secrets/n8n.env.sops  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/servicehub/secrets/n8n.env.sops

### Traefik
- Homelab_Traefik.md  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/Homelab_Traefik.md
- hosts/traefik/README.md  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/README.md
- deploy.sh  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/scripts/deploy.sh
- test.sh  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/scripts/test.sh
- compose/docker-compose.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/compose/docker-compose.yml
- secrets/compose.env.sops  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/secrets/compose.env.sops
- static/traefik.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/config/static/traefik.yml

Dynamic config:
- mw-authentik.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/config/dynamic/mw-authentik.yml
- traefik.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/config/dynamic/traefik.yml
- authentik.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/config/dynamic/authentik.yml
- proxmox.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/config/dynamic/proxmox.yml
- truenas.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/config/dynamic/truenas.yml
- bitwarden.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/config/dynamic/bitwarden.yml
- wikijs.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/config/dynamic/wikijs.yml
- n8n.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/config/dynamic/n8n.yml
- forgejo.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/config/dynamic/forgejo.yml
- gitlab.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/config/dynamic/gitlab.yml
- gitlab-registry.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/config/dynamic/gitlab-registry.yml
- games-tcp.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/config/dynamic/games-tcp.yml
- games-udp.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/config/dynamic/games-udp.yml
- ue5-udp.yml  
  https://raw.githubusercontent.com/Versigable/homelab-infra/main/hosts/traefik/config/dynamic/ue5-udp.yml

### GitLab (host folder)
- (empty currently; add links here as soon as you add compose/scripts/secrets)

---

## Notes

- If a file moves, update the link here so future reviews stay one-click.
- Keep secrets encrypted (`*.sops`) and never add decrypted `.env` to Git.
