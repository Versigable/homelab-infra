# webstation (RomM emulator streaming)

Files `scripts/deploy-webstation.sh` installs (every file in these three folders) under `/home/jf/webstation-hub/` and
`compose/webstation.yml` mounts read-only into the container. All three fill gaps in
the romm-broker shipped with `lscr.io/linuxserver/webstation:romm-version-v0.15.0`:

| File | Mounted at | Why |
|------|-----------|-----|
| `custom-cont-init.d/50-seed-emulators` | `/custom-cont-init.d` | Runs as root at every start. Copies the PS1, PS2 and Dreamcast BIOS from the RomM library into DuckStation, PCSX2, Flycast and RetroArch, and pins PCSX2 to the USA SCPH-39001 BIOS. Installs PCSX2's `patches.zip` (upstream's rolling release, validated, refreshed weekly). Sets up xemu: `xemu.toml` with the library's MCPX and Complex 4627 images, a blank HDD, and port 1 on the virtual pad. Installs the full PPSSPP and Wii control maps, and sets PPSSPP's memory stick to 1 GB. Copy-if-missing only. |
| `custom-services.d/svc-ppsspp-shm-reaper` | `/custom-services.d` | s6 service. Deletes `/dev/shm/PPSSPP_ID` whenever PPSSPP isn't running; otherwise every launch after a broker stop is a "secondary instance" with game volume 0. |
| `seed/ppsspp-controls.ini` | `/seed` | PPSSPP 1.20.4's default Linux map (QWERTY keyboard + pad 0) plus the broker's `[`/`]` save/load keys, generated from the v1.20.4 sources. The broker's own seed holds only those two keys, and PPSSPP unbinds everything a `[ControlMapping]` section leaves out. |
| `seed/dolphin-WiimoteNew.ini` | `/seed` | Wiimotes 1-4 as emulated Wiimotes with a Classic Controller, bound to SDL pads 0-3 using the names the broker's own GameCube template uses (`SDL/<n>/Xbox 360 Controller`). The broker configures only GameCube pads, leaving Wii games on Dolphin's keyboard/mouse default. |
| `seed/xemu-xbox_hdd.qcow2.zip` | `/seed` | xemu's official blank 8 GiB HDD (xemu-project/xemu-hdd-image 1.0, sha256 `d9f5a4c1…bb39`). It's extracted to `/config/xemu/xbox_hdd.qcow2` once, and the broker converts it to raw on first launch. |

The container's `/config` (cores, BIOS copies, emulator settings, broker exports) lives
in `/home/jf/webstation-hub/config` and is not in git.

Changing any of these files restarts the container on deploy, ending any running session.

PCSX2 reads `patches.zip` only from `/usr/share/PCSX2/resources/`. The broker's own refresh installs it with `sudo`, which `DISABLE_SUDO=true` blocks, so compose sets `PCSX2_PATCHES_FETCH=false` and the cont-init script fetches it as root. It sits in the container layer, so a recreate fetches it again.

Azahar (3DS) needs nothing here: the image seeds `qt-config.ini` from `/defaults/azahar.ini`, which already binds the virtual pad (GUID `030081b85e0400008e02000014010000`) through SDL's controller API.
