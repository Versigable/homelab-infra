# Operator keys

Public keys of the IaC control nodes, one file per operator. Every Terraform-built guest's
`ansible` user accepts exactly these keys (role `base`, `exclusive`), and Terraform puts them
on new guests. Adding a file here and running `site.yml` grants access to every guest; removing
one and running it revokes access. Private keys stay on their control node.

| File | Control node |
|---|---|
| `homelab-iac.pub` | WSL on MetaVersig's workstation (since 2026-10-17) |
| `homelab-iac-fk.pub` | fk-claude, Firekube's Claude Code VM (since 2026-10-23) |

## Scoped keys

A subdirectory is a scope: its keys are accepted only on the hosts whose groups list that
scope in `base_admin_key_scopes` (role `base`), on top of the operators' keys. Terraform never
puts them on new guests. They belong to deploy runners, which apply one part of the IaC from
CI and must not reach the rest.

| Scope | Hosts | Holder |
|---|---|---|
| `studio/` | studio-gw, the hub, the Forge, the cells (`group_vars/studio_gw.yml`, `studio_private.yml`) | `iac-runner` (LXC 119): applies the studio when a merge touches it (role `iac_runner`) |
