# Operator keys

Public keys of the IaC control nodes, one file per operator. Every Terraform-built guest's
`ansible` user accepts exactly these keys (role `base`, `exclusive`), and Terraform puts them
on new guests. Adding a file here and running `site.yml` grants access to every guest; removing
one and running it revokes access. Private keys stay on their control node.

| File | Control node |
|---|---|
| `homelab-iac.pub` | WSL on MetaVersig's workstation (since 2026-10-17) |
| `homelab-iac-fk.pub` | fk-claude, Firekube's Claude Code VM (since 2026-10-23) |
