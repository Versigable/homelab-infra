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
  terraform/modules/vm/    reusable VM, cloned from the node's cloud-init template
  terraform/xmansion/      root module for X-Mansion (phoenix + rogue); guests.tf is the guest list
  terraform/wolverine/     root module for wolverine (MetaVersig's house, LAB 10.1.0.0/16 on vmbr1)
  ansible/                 inventory (nodes.yml static; guests.yml and guests-<site>.yml generated), roles, playbooks
  scripts/iac.sh           runs everything with the right credentials and settings
```

## LXC or VM

A guest that **writes to TrueNAS over NFS is a VM**. An LXC's NFS mount is the node's own NFS
client, and TrueNAS runs on that node: on 2026-10-23 a node-side writer deadlocked TrueNAS for
about 30 minutes. A VM's NFS client keeps its dirty pages in guest RAM. Everything else can be
an LXC (cheaper), including guests that only read from TrueNAS (their node mount is `ro: true`).

VMs are cloned from template 9000 (Debian 13 cloud image, built by `pve-templates.yml`).
cloud-init creates the `ansible` user with the operators' keys, so a VM skips `iac.sh bootstrap`.
It mounts its own shares (role `nfs_client`), and Docker waits for them (`RequiresMountsFor`).

## The studio network

The game studio's guests (`terraform/xmansion/studio.tf`, VMIDs 150-169) live on their own
network: VNet `studio0` (10.77.0.0/24) in the SDN zone `studio`, a VXLAN between phoenix and
rogue. Nothing on it has a route to `vmbr0`; `studio-gw` (VM 150, 10.0.1.150 on the LAN) is
its only router, with an egress allow-list by hostname (`group_vars/studio_gw.yml`). Ansible
reaches guests on it through `studio-gw` (`group_vars/studio_private.yml`). The Forge (VM 155)
holds the RTX 5070 Ti by PCI mapping `gpu-5070ti`. It has no GitLab runner: its lanes are
platform, so Ansible owns them. Vault: `1-Projects/game-studio-platform/design/`.

The zone, the vnet and the GPU mapping are cluster objects the IaC token can't create;
`pve-cluster.yml` makes them as root and grants the token use of them. Applying SDN reloads
networking on every node, so that playbook stops until you re-run it with
`-e pve_sdn_apply=true`.

## Access and state

- **Proxmox:** user `iac@pve`, token `infra` (privilege-separated). Role `InfraIaC` on pool
  `iac` and `local-lvm`; `PVEAuditor` on `local`, `/nodes/phoenix` and
  `/nodes/wolverine`; `PVESDNUser` on `vmbr0`, `vmbr1` (wolverine's LAB; no other node has
  one) and the studio vnet `studio0`; `PVEMappingUser` on the PCI mapping `gpu-5070ti`.
  It can't touch guests outside the `iac` pool.
- **Terraform state:** GitLab-managed, one state per site, in a state-only GitLab project.
  Writing state needs the Maintainer role, and homelab-infra's agent token is
  deliberately only a Developer, so only humans can merge to `main`.
- **Ansible:** reaches guests as `ansible` with the operator's own key (`iac/keys`, one per control node; `IAC_SSH_KEY` picks the private key, default `~/.ssh/homelab-iac`) (root SSH is off after
  bootstrap), and reaches nodes as `claude-agent` for the pilot.
- **Nothing secret lives in this directory.** The repo has a public mirror.

## Control node setup (once per operator)

- terraform ≥ 1.9 and an Ansible venv (`ansible-galaxy collection install -r ansible/requirements.yml`)
- `~/.config/homelab-iac/pve-token` (0600): `iac@pve!<token>=<secret>` (one token per control node: `infra` on MetaVersig's WSL, `infra-fk` on fk-claude, same ACLs)
- `~/.config/homelab-iac/state.env`: `GITLAB_URL=http://10.0.1.13:8081` and `IAC_STATE_PROJECT=<id>`
- `GITLAB_TOKEN_FILE` pointing at an env file with `GITLAB_TOKEN_IACSTATE=...` (and runner tokens)
- your own guest key (its `.pub` committed in `iac/keys`, then `site.yml` run once by an operator who already has access) and `~/.ssh/claude-agent` (the nodes' key)

## Building a guest

1. Add it to `terraform/<site>/guests.tf`, plus `ansible/inventory/host_vars/<name>.yml` if it
   needs root-only settings, and add its roles to `site.yml`. Open an MR with the plan output.
2. After merge:
   ```
   iac.sh play pve-templates.yml      # templates on the nodes
   iac.sh play pve-cluster.yml        # SDN zones/vnets, PCI mappings, their ACLs (no-op if unchanged)
   iac.sh tf init && iac.sh tf apply  # the guest
   iac.sh inventory                   # Ansible guest inventory from Terraform
   iac.sh bootstrap <name>            # first contact as root; creates the ansible user
   iac.sh play site.yml               # node plumbing + guest platform
   ```
3. Run it again: `iac.sh tf plan` shows no changes and `site.yml` reports `changed=0`.

For a site other than xmansion, prefix the `tf` and `inventory` steps with its name, e.g.
`IAC_SITE=wolverine iac.sh tf apply`. Each site has its own state and its own inventory file;
the playbooks run against all of them.

After a rebuild the guest has a new SOPS age key, so its `.sops.yaml` rule and secrets
files must be re-encrypted to the new public key. `site.yml` prints the key.
