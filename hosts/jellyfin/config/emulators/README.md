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
| `azahar/` | `~jf/.var/app/org.azahar_emu.Azahar/config/azahar-emu/` |
| `cemu/` | `~jf/.var/app/info.cemu.Cemu/config/Cemu/` |
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

## PSP: "not enough memory stick space" — set `MemStickSize = 1`

Sonic Rivals 2 refused to start 2026-09-07, demanding **256 KB free** on the
memory stick. Nothing real was wrong: 184 G free on disk, the flatpak sandbox saw
the same, `MemStickInserted = True`, `SAVEDATA` present and writable.

**`MemStickSize` is the size PPSSPP *reports* to the game, in GB.** At 32 it
claims ~34 billion bytes free. Many PSP-era titles hold free space in a **signed
32-bit int** (max ~2.147e9), so a large stick overflows it and the game reads a
negative or garbage value — concluding it cannot fit 256 KB. Real sticks were
usually 1-4 GB, so plenty of games were never tested against 32 GB.

Fixed by **Settings → System → Memory Stick size → 1 GB**. Confirmed working.
Affects only what PPSSPP reports; actual disk usage is unchanged, and 1 GB is
still far more than any PSP game needs. Left at 1 as the library-wide default
rather than a per-game workaround.

Treat this error as a **reported-size** problem first — permissions, sandbox
paths and real free space were all red herrings here.

### Do not edit `ppsspp.ini` while PPSSPP is running

It holds config in memory and rewrites the file on exit, silently discarding
external edits. This clobbered two changes on 2026-09-07. Change settings in the
app, or close it first.

## Azahar (3DS) and Cemu (Wii U) — added 2026-09-20

```
org.azahar_emu.Azahar   2126.1.1   gamedir -> /mnt/gameroom/roms/3ds
info.cemu.Cemu          2.6        GamePaths/Entry -> /mnt/gameroom/roms/wiiu
```

Both installed from Flathub and given `flatpak override --filesystem=/mnt/gameroom`,
same as Flycast/PCSX2/PPSSPP. Both added to `../sunshine/apps.json`.

### 3DS ROMs must be DECRYPTED — keys are not a workaround

Scene `.3ds` releases (LiGHTFORCE, VENOM, anything "tested with Gateway") are
**encrypted cartridge dumps**. Azahar lists them but refuses to boot:

```
Core <Critical> core/core.cpp:Load:353: Failed to determine system mode (Error 8)!
```

`aes_keys.txt` (98 `slot0x*` entries) **and** `boot9.bin` were installed to
Azahar's sysdata and confirmed readable from inside the flatpak sandbox — the
ROM still failed with the same error. Treat a decrypted dump as a hard
requirement. The keys are kept anyway for CIA installs and title-key content.

Check before downloading, no emulator needed (partition 0 at `0x4000`, NCCH
flags at NCCH+`0x188`):

```bash
dd if=rom.3ds bs=1 skip=256   count=4 2>/dev/null | od -c        # N C S D
dd if=rom.3ds bs=1 skip=16640 count=4 2>/dev/null | od -c        # N C C H
dd if=rom.3ds bs=1 skip=16776 count=8 2>/dev/null | od -An -tx1  # flags
```

`flags[7]` bit `0x04` = NoCrypto: set means decrypted and bootable, `00` means
it will not boot. `flags[3]` is the crypto method (`0x01` = 7.x keys). A `.3dz`
extension is just a renamed `.3ds` — check the flags, not the extension.

### Keys and BIOS are NOT tracked here

`aes_keys.txt`, `boot9.bin` and the Wii U `keys.txt` live on the host at
`/mnt/gameroom/bios/<slug>/` and in each emulator's sysdata. They are
BIOS-class binaries and stay out of this repo, like every other console BIOS.
