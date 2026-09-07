# Sunshine game-stream host (jf VM)

Native package (`sunshine`, LizardByte apt repo) on the jf VM, streaming to
Moonlight clients. **Not containerised** — it needs direct GPU + X access, so it
runs as three systemd **user** units under `jf` (lingering enabled).

Emulators too heavy for RomM's browser EmulatorJS live here instead: Dolphin,
PCSX2, Flycast and PPSSPP. See
`3-Resources/runbooks/sunshine-emulator-streaming` in the vault.

## Layout on the host

| repo path | host path |
|---|---|
| `systemd/*.service` | `/home/jf/.config/systemd/user/` |
| `config/sunshine/apps.json` | `/home/jf/.config/sunshine/apps.json` |
| `config/sunshine/sunshine.conf` | `/home/jf/.config/sunshine/sunshine.conf` |

`sunshine_state.json` is deliberately **not** tracked — it holds the web-UI
credential hash and paired-client certificates.

## Install / restore

```bash
install -m 0644 systemd/*.service        /home/jf/.config/systemd/user/
install -m 0644 config/sunshine/*        /home/jf/.config/sunshine/
sudo loginctl enable-linger jf
sudo -u jf XDG_RUNTIME_DIR=/run/user/$(id -u jf) systemctl --user daemon-reload
sudo -u jf XDG_RUNTIME_DIR=/run/user/$(id -u jf) systemctl --user enable --now \
     xorg-headless openbox-session sunshine
```

`deploy.sh` does not manage these — same convention as
`hosts/servicehub/systemd/romm-db-backup.*`. Tracked here for review and rebuild.

## Ordering-cycle bug (fixed 2026-09-06)

`xorg-headless.service` originally had **both** `After=default.target` and
`WantedBy=default.target`. A unit pulled in by a target cannot also be ordered
after it, so systemd resolved the cycle by **discarding sunshine.service's start
job at every boot** — the stack was silently dead from ~2026-08-26 until it was
found. Only bare Xorg came up.

    default.target: Found ordering cycle on sunshine.service/start
    default.target: Job sunshine.service/start deleted to break ordering cycle

`StartLimitIntervalSec`/`StartLimitBurst` were also in `[Service]`; since
systemd v230 they belong in `[Unit]` and were silently ignored.

If Sunshine is ever inactive after a boot, check for `After=default.target`
reappearing in `xorg-headless.service` first.

## Notes

- Web UI on :47990, user `sunshine_admin`. Password is hashed in
  `sunshine_state.json` and is **not recoverable** — reset with
  `sunshine --creds <user> <pass>`, then restart the unit.
- Public route `sunshine.ninjaprivacy.org` is Authentik-gated
  (`hosts/traefik/config/dynamic/sunshine.yml`). The stream itself stays on LAN;
  only the config UI traverses the tunnel. That UI can define apps that execute
  arbitrary commands as `jf` — keep it gated.
- Emulator flatpaks need `flatpak override --filesystem=/mnt/gameroom`.
