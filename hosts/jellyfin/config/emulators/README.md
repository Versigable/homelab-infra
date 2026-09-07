# Emulator + session configs (jf VM)

Configs for the emulators launched via Sunshine, plus the Openbox session they
run inside. Tracked so a rebuild reproduces a tuned host rather than defaults.

Same convention as `../../systemd/`: **tracked for review and rebuild, not
auto-deployed.** `deploy.sh` handles compose stacks only.

## Host paths

| repo path | host path |
|---|---|
| `ppsspp/` | `~jf/.var/app/org.ppsspp.PPSSPP/config/ppsspp/PSP/SYSTEM/` |
| `pcsx2/` | `~jf/.var/app/net.pcsx2.PCSX2/config/PCSX2/inis/` |
| `flycast/` | `~jf/.var/app/org.flycast.Flycast/config/flycast/` |
| `dolphin/` | `~jf/.config/dolphin-emu/` |
| `openbox/rc.xml` | `~jf/.config/openbox/rc.xml` |

Flatpak apps must be launched once before their config dir exists. Each also
needs `flatpak override --filesystem=/mnt/gameroom` or it cannot see the ROMs.

## PPSSPP settings that matter (tuned 2026-09-07)

```
GraphicsBackend    = 3        Vulkan - materially faster than OpenGL here
InternalResolution = 4        4x (1920x1088). 16x the pixels of native PSP;
                              the 3060 Ti handles it, but a few heavy 3D
                              titles may want 3x
FrameSkip          = 0        off. It was only ever a workaround for the
                              browser-WASM path, which this host replaces
FullScreen         = True     fills the 1920x1080 capture; a windowed app
                              wastes both resolution and encoder bandwidth
CurrentDirectory   = /mnt/gameroom/roms/psp
```

## Why these live here at all

PSP is too heavy for RomM's browser EmulatorJS - it is CPU-side WASM with no
GPU access, and no combination of frameskip or internal-resolution settings made
it playable. Native PPSSPP on the RTX 3060 Ti, streamed via Sunshine, is the
supported path. Same reasoning already applied to PS2, GameCube and Dreamcast.

Browser play remains correct for PS1, N64, Saturn and the 8/16-bit systems.

## Not tracked

- `Qt.ini` (Dolphin window geometry / recent files) and `TimePlayed.ini` - churn
- Zero-byte defaults: `DSUClient.ini`, `FreeLook.ini`, `RetroAchievements.ini`
- Save states and memory cards - user data, not config

## Note

All credential fields in these files are empty (`Token`, `UserName`,
`AchievementsUserName`, `InfrastructureUsername`). `ISPUsername = flycast1` is a
Flycast default for Dreamcast dial-up emulation, not an account. PPSSPP's
`MacAddress` is a generated identity for the emulated PSP, not host hardware.
