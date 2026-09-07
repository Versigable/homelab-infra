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

## Version pinning: stay on 2025.924.154138 until the NVIDIA driver is upgraded

**Do not upgrade to Sunshine 2026.906.222525 or later on this host yet.**

Attempted 2026-09-07 and rolled back the same hour. The newer build is compiled
against a newer NVENC SDK and **cannot use hardware encoding on driver 550**:

    [h264_nvenc] Driver does not support the required nvenc API version.
                 Required: 13.1  Found: 12.2
                 The minimum required Nvidia driver for nvenc is 610.00 or newer

It silently falls through nvenc -> vulkan -> vaapi and lands on **software
libx264**, which on this VM's 8 vCPUs is worse than not streaming at all. The
release notes' "continued NVENC support for older GPUs" fix (#5451) is
**Windows-only**; Linux gets no such fallback.

Rollback is clean: `dpkg -i --force-downgrade` the older `.deb`. The binary is a
symlink (`/usr/bin/sunshine -> sunshine-<version>`), config and units are
untouched by either direction.

jf currently runs driver **550.163.01**. Upgrading to 610+ is NOT a small job:
the RTX 3060 Ti is `vfio-pci` passed through to this VM and Jellyfin uses the
same GPU for NVENC transcoding, so a driver change risks the media server too.
Treat it as a maintenance window, not an evening task.

Once on 610+, 2026.906 is strictly better - it carries **five security
advisories** (GHSA-6w33-pjh7-p77c, GHSA-6jvv-jqr7-m6m3, GHSA-36ff-frg7-492f,
GHSA-26q2-58j6-qmvv, GHSA-c428-87f8-rrv5) plus hardware YUV 4:4:4 / HDR
encoding on NVIDIA Linux. Staying on 2025.924 is a deliberate trade, tolerable
only because `sunshine.ninjaprivacy.org` is Authentik-gated.

### Migration notes for when that happens

- **The 2026.x package ships its own unit**, `/usr/lib/systemd/user/app-dev.lizardbyte.app.Sunshine.service`.
  2025.924 shipped none. Our `~/.config/systemd/user/sunshine.service` still wins
  (user scope outranks `/usr/lib`), and `systemctl --user show sunshine -p FragmentPath`
  confirmed that after the upgrade - but verify it rather than assume, and
  consider whether to adopt the packaged unit instead.
- **`output_name = 0` must change.** Numeric display indices are deprecated and
  may reorder; use a connector name such as `DP-1`.
- `ds5_inputtino_randomize_mac` was renamed `virtualhid_randomize_mac` (not set here).
- Input moves to libvirtualhid; controller identity may change and Moonlight
  gamepad mappings may need redoing.
