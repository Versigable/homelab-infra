# N64 core for the webstations (homelab build)

The webstations play N64 on RetroArch's mupen64plus-next core. Its bundled GLideN64 video
plugin was last synced from upstream in January 2025, so it lacks upstream fixes that show in
games we have. This folder builds libretro's own core with five of them added, nothing else.

| Game | Bug without the fix | Upstream fix | Kind |
|---|---|---|---|
| Mario Kart 64 | 2-4 player split screen: item box, minimap and lap/time draw as black or blank boxes ([libretro #593](https://github.com/libretro/mupen64plus-libretro-nx/issues/593), seen 2026-11-27) | [gonetz/GLideN64@5256259](https://github.com/gonetz/GLideN64/commit/525625907e9c11ef1326d9775276d0bfed7beba3): the second (split-screen) microcode, CRC `0xf89248e1`, added to `specialMicrocodes` | code, 1 line |
| Super Mario 64 | water bomb texture faces the wrong way (HLE) | [@430798d](https://github.com/gonetz/GLideN64/commit/430798d), `RSP.cpp` hunk only (its `gSP.h` hunk just deletes unused fields) | code, 2 lines |
| Banjo-Kazooie | puzzle transitions | [@e8a5e82](https://github.com/gonetz/GLideN64/commit/e8a5e82): `copyToRDRAM=1` | per-game setting |
| Majora's Mask (USA/EU, JP) | Pictograph Box delay | [@a04081a](https://github.com/gonetz/GLideN64/commit/a04081a): `copyAuxToRDRAM=1` | per-game setting |
| Quest 64 / Holy Magic Century / Eltale Monsters | pink text | [@0570620](https://github.com/gonetz/GLideN64/commit/0570620): `enableHalosRemoval=1` | per-game setting |

The per-game settings go into `custom/GLideN64/GLideN64.custom.ini.h`, the copy of
GLideN64's ini that the core compiles in and applies over the RetroArch core options.

## Files

- `mupen64plus-next-homelab.patch`: the five fixes, against libretro commit `12edd2c`
  (2026-10-01), the commit the 10-12 nightly we ran before was built from.
- `build.sh`: builds the core in a throwaway Ubuntu 20.04 container (gcc 9, the flags and
  strip of libretro's Linux x86_64 CI). Run on jf: `sudo ./build.sh /var/tmp/n64core-build/out`.
  The version string reads `3.0-Vulkan 12edd2c-homelab1`; bump `BUILD_TAG` when the patch changes.
- `custom-cont-init.d/48-n64-core-check` (in the webstation config) logs at every start whether
  the installed core is this build.

## Install (per webstation, when idle)

The core lives in each webstation's `/config/.config/retroarch/cores/mupen64plus_next_libretro.so`
(owner abc, 0755). `/config` survives container recreates. The broker downloads a core only when
that file is missing and never replaces it. Keep the replaced core beside it as
`mupen64plus_next_libretro.so.libretro-<commit>` to roll back.

The Forge has no internet: copy the file to it, don't let its broker download.

Saves and save states are unaffected. The fixes touch only the video plugin and its settings,
and neither is stored in in-game saves or states, so players can move between this build and
libretro's in either direction.

## Going back to libretro's builds

When libretro's `develop` has the fixes (check `GLideN64/src/GBI.cpp` for `0xf89248e1`, and
`custom/GLideN64/GLideN64.custom.ini.h` for `BANJO-KAZOOIE`, `QUEST%2064`,
`ZELDA%20MAJORA%27S%20MASK`), and its buildbot nightly is newer than that commit:

1. On each webstation, delete `mupen64plus_next_libretro.so` (and the `.libretro-*` backup).
   jf's download the nightly at their next N64 launch; copy jf's to the Forge.
2. Remove this folder and `48-n64-core-check` in one MR.

The two parts can land upstream separately (libretro updates its GLideN64 copy and its ini
copy in separate commits); if only the Mario Kart line lands, weigh the other four.
