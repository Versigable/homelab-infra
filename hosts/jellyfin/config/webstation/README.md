# webstation (RomM emulator streaming)

Files `scripts/deploy-webstation.sh` installs (every file in these three folders) under `/home/jf/webstation-hub/` and
`compose/webstation.yml` mounts read-only into the container. All three fill gaps in
the romm-broker shipped with `lscr.io/linuxserver/webstation:romm-version-v0.15.0`:

| File | Mounted at | Why |
|------|-----------|-----|
| `custom-cont-init.d/50-seed-emulators` | `/custom-cont-init.d` | Runs as root at every start. Copies the PS1, PS2 and Dreamcast BIOS from the RomM library into DuckStation, PCSX2, Flycast and RetroArch, pins PCSX2 to the USA SCPH-39001 BIOS, installs the full PPSSPP and Wii control maps, and sets PPSSPP's memory stick to 1 GB. Copy-if-missing only. |
| `custom-services.d/svc-ppsspp-shm-reaper` | `/custom-services.d` | s6 service. Deletes `/dev/shm/PPSSPP_ID` whenever PPSSPP isn't running; otherwise every launch after a broker stop is a "secondary instance" with game volume 0. |
| `seed/ppsspp-controls.ini` | `/seed` | PPSSPP 1.20.4's default Linux map (QWERTY keyboard + pad 0) plus the broker's `[`/`]` save/load keys, generated from the v1.20.4 sources. The broker's own seed holds only those two keys, and PPSSPP unbinds everything a `[ControlMapping]` section leaves out. |
| `seed/dolphin-WiimoteNew.ini` | `/seed` | Wiimotes 1-4 as emulated Wiimotes with a Classic Controller, bound to SDL pads 0-3 using the names the broker's own GameCube template uses (`SDL/<n>/Xbox 360 Controller`). The broker configures only GameCube pads, leaving Wii games on Dolphin's keyboard/mouse default. |

The container's `/config` (cores, BIOS copies, emulator settings, broker exports) lives
in `/home/jf/webstation-hub/config` and is not in git.

Changing any of these files restarts the container on deploy, ending any running session.
