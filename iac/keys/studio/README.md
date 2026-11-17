# Scope `studio`

Keys accepted by the `ansible` user on the studio's hosts only (studio-gw, studio-hub,
studio-forge and the cells), through `base_admin_key_scopes: [studio]` in
`group_vars/studio_gw.yml` and `group_vars/studio_private.yml`. No other guest accepts them.

| File | Holder |
|---|---|
| `iac-runner.pub` (added once the runner exists) | `iac-runner` (LXC 119, 10.0.1.19): the protected runner that applies the studio from homelab-infra CI. The key is generated on it (`/etc/homelab-iac/id_studio`, role `iac_runner`) and never leaves it |

Removing a file here and running `site.yml` on the studio hosts revokes that key.
