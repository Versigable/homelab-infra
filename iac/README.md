# iac/ — guests built from code

Since 2026-10-17 every new VM and LXC is built with Terraform + Ansible, never by hand
(vault decision `5-Decisions/2026-10-17_terraform-ansible-for-new-guests`). Nextcloud
(LXC 110) is the pilot; the written standard comes out of it.

## Who owns what

| Layer | Tool | Owns |
|---|---|---|
| Guest | Terraform, `terraform/<site>/` | The VM/LXC itself: size, disk, NIC, template, pool, tags |
| Node plumbing | Ansible, `pve_*` roles on `proxmox_nodes` | Templates, NFS mounts, container settings Proxmox reserves for root |
| Guest platform | Ansible, `site.yml` on the guest | Base OS, Docker, GitLab runner, SOPS age key, service layout |
| App | GitOps CI, `hosts/<host>/` | Compose stack, secrets, config (unchanged) |

Proxmox refuses some container settings to API tokens: feature flags other than
`nesting`, and bind mounts. Ansible sets them on the node as root, and the Terraform
module ignores those attributes so the two never fight.

## Layout

```
iac/
  terraform/modules/lxc/   reusable unprivileged LXC
  terraform/xmansion/      root module for X-Mansion (phoenix + rogue); guests.tf is the guest list
  ansible/                 inventory (nodes.yml static, guests.yml generated), roles, playbooks
  scripts/iac.sh           runs everything with the right credentials and settings
```

## Access and state

- **Proxmox:** user `iac@pve`, token `infra` (privilege-separated). Role `InfraIaC` on pool
  `iac` and `local-lvm`; `PVEAuditor` on `local` and `/nodes/phoenix`; `PVESDNUser` on
  `vmbr0`. It can't touch guests outside the `iac` pool.
- **Terraform state:** GitLab-managed, one state per site, in a state-only GitLab project.
  Writing state needs the Maintainer role, and homelab-infra's agent token is
  deliberately only a Developer, so only humans can merge to `main`.
- **Ansible:** reaches guests as `ansible` with the `homelab-iac` key (root SSH is off after
  bootstrap), and reaches nodes as `claude-agent` for the pilot.
- **Nothing secret lives in this directory.** The repo has a public mirror.

## Control node setup (once per operator)

- terraform ≥ 1.9 and an Ansible venv (`ansible-galaxy collection install -r ansible/requirements.yml`)
- `~/.config/homelab-iac/pve-token` (0600): `iac@pve!infra=<secret>`
- `~/.config/homelab-iac/state.env`: `GITLAB_URL=http://10.0.1.13:8081` and `IAC_STATE_PROJECT=<id>`
- `GITLAB_TOKEN_FILE` pointing at an env file with `GITLAB_TOKEN_IACSTATE=...` (and runner tokens)
- `~/.ssh/homelab-iac` (the guests' key) and `~/.ssh/claude-agent` (the nodes' key)

## Building a guest

1. Add it to `terraform/<site>/guests.tf`, plus `ansible/inventory/host_vars/<name>.yml` if it
   needs root-only settings, and add its roles to `site.yml`. Open an MR with the plan output.
2. After merge:
   ```
   iac.sh play pve-templates.yml      # templates on the nodes
   iac.sh tf init && iac.sh tf apply  # the guest
   iac.sh inventory                   # Ansible guest inventory from Terraform
   iac.sh bootstrap <name>            # first contact as root; creates the ansible user
   iac.sh play site.yml               # node plumbing + guest platform
   ```
3. Run it again: `iac.sh tf plan` shows no changes and `site.yml` reports `changed=0`.

After a rebuild the guest has a new SOPS age key, so its `.sops.yaml` rule and secrets
files must be re-encrypted to the new public key. `site.yml` prints the key.
