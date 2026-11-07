# webstation (RomM emulator streaming)

Files `scripts/deploy-webstation.sh` installs (every file in these four folders) under `/home/jf/webstation-hub/` and
`compose/webstation.yml` mounts read-only into all three containers. They fill gaps in
the romm-broker shipped with `lscr.io/linuxserver/webstation:romm-version-v0.15.0`:

| File | Mounted at | Why |
|------|-----------|-----|
| `custom-cont-init.d/40-webrtc-public-ip` | `/custom-cont-init.d` | Runs as root at every start; does nothing unless the instance sets `SELKIES_MODE=webrtc`. Resolves `WEBRTC_PUBLIC_HOST` with `bin/webrtc-public-ip` and writes the address to `SELKIES_WEBRTC_PUBLIC_IP`, which Selkies accepts only as an IP. If the name gives no public IPv4 (missing record, or a proxied record answering with Cloudflare's IPs), the instance falls back to websockets mode. See "WebRTC over UDP" below. |
| `custom-cont-init.d/45-broker-full-save-export` | `/custom-cont-init.d` | Runs as root at every start, before the broker. Patches one line of the romm-broker (`api.py`, in the container layer) so that on Xbox, Wii, 3DS and PSP the exit dump ships the game's **whole** save tree. Otherwise it ships only the files the session wrote, while RomM restores only the newest archive. So a session that left a file alone made the next launch lose it (Shenmue II lost save slot 1, 2026-11-07). That's safe because those emulators wipe the game's save tree before each restore (`clears_stale_saves`). GameCube and PS2 don't need it: RomM's whole-card sync covers them (`memory_card_sync` per platform in RomM's `config.yml`). Idempotent; if a new image changes the line, it logs and leaves the broker alone. |
| `custom-cont-init.d/50-seed-emulators` | `/custom-cont-init.d` | Runs as root at every start. Copies the PS1, PS2 and Dreamcast BIOS from the RomM library into DuckStation, PCSX2, Flycast and RetroArch, and pins PCSX2 to the USA SCPH-39001 BIOS. Installs PCSX2's `patches.zip` (upstream's rolling release, validated, refreshed weekly). Sets up xemu: `xemu.toml` with the library's MCPX and Complex 4627 images, a blank HDD, and port 1 on the virtual pad. Installs the full PPSSPP and Wii control maps, and sets PPSSPP's memory stick to 1 GB. Copies RetroArch's controller profiles and menu assets from the image (`/usr/share/libretro`) when `/config` has none; without the profiles RetroArch ignores Selkies' virtual pads ("Microsoft X-Box 360 pad (0/0) not configured"). Adds the performance defaults below to Dolphin's `GFX.ini`, `PCSX2.ini` and `xemu.toml`. Copy-if-missing or set-if-missing only, except the three Selkies page scripts (gamepad shim, video sink, playout buffer), which it installs into the Selkies web root (`/usr/share/selkies/web`, container layer) and loads from `index.html` on every start. |
| `custom-services.d/svc-ppsspp-shm-reaper` | `/custom-services.d` | s6 service. Deletes `/dev/shm/PPSSPP_ID` whenever PPSSPP isn't running; otherwise every launch after a broker stop is a "secondary instance" with game volume 0. |
| `custom-services.d/svc-webrtc-ip-watch` | `/custom-services.d` | s6 service; sleeps on instances without `WEBRTC_PUBLIC_HOST`. Every 5 minutes it re-resolves the public address. When the WAN IP changes (DDNS), it writes the new one and restarts Selkies at once, since sessions on the old address are already broken. When the instance fell back to websockets at start and the name resolves again, it restarts Selkies into WebRTC, but only while nobody is connected. |
| `seed/ppsspp-controls.ini` | `/seed` | PPSSPP 1.20.4's default Linux map (QWERTY keyboard + pad 0) plus the broker's `[`/`]` save/load keys, generated from the v1.20.4 sources. The broker's own seed holds only those two keys, and PPSSPP unbinds everything a `[ControlMapping]` section leaves out. |
| `seed/dolphin-WiimoteNew.ini` | `/seed` | Wiimotes 1-4 as emulated Wiimotes with a Classic Controller, bound to SDL pads 0-3 using the names the broker's own GameCube template uses (`SDL/<n>/Xbox 360 Controller`). The broker configures only GameCube pads, leaving Wii games on Dolphin's keyboard/mouse default. |
| `seed/selkies-gamepad-shim.js` | `/seed` | Loaded by the Selkies page ahead of its own bundle. Rewrites controllers Chrome reports without a standard layout into the standard one, from per-controller profiles keyed by OS, browser and USB vendor/product ID. Without it, the Selkies client turns raw axes 4/5 into the d-pad and ignores hats, so a PowerA GameCube-style pad (`20d6:a711`) has its C-stick driving the d-pad and no working d-pad, and its stock jsdb profile crosses A and B in Dolphin. Profiled: PowerA GameCube-style wired, Chrome on Windows. To add a pad, read `navigator.getGamepads()` on that machine while pressing each control. |
| `seed/selkies-video-sink.js` | `/seed` | Loaded by the Selkies page ahead of its own bundle. Hides `MediaStreamTrackGenerator`, so Chromium browsers (Chrome, Brave, Edge) decode and draw full-frame video in the Selkies video worker on an OffscreenCanvas, the path Firefox takes. Otherwise they decode on the page's main thread, where a backed-up decoder drops frames until a key frame arrives, and feed a `<video>` element. On that path, streams froze for about a second at a time while the bytes kept arriving. Firefox froze much less (2026-10-12). The client's `[Selkies] video sink: …` console line shows the path in use. If the worker fails, Chromium now falls back to the page's 2D canvas, not to the `<video>`. To compare in one browser, run `localStorage.setItem("homelab.videoSink", "mstg")` in the stream frame's console and reload (`localStorage.removeItem("homelab.videoSink")` to undo). |
| `seed/selkies-playout-buffer.js` | `/seed` | Loaded by the Selkies page ahead of its own bundle. Patches the client's video worker (by wrapping `Blob` when the client builds it from source) so decoded frames go through a small playout buffer: each gets a due time one measured frame period after the last, held about 50 ms behind its arrival, and frames are drawn on the display's refresh. Without it the worker draws each frame the moment it's decoded, so frames that arrive bunched (WebSocket over Cloudflare's tunnel, a TLS-scanning antivirus) show as hitches although the server sends a steady 60 fps. A late frame is shown at once; after a burst the clock drops back to the target rather than letting latency pile up. Costs about 50 ms (the default since 2026-10-25: 25 ms was enough on a wired link, not for a co-op friend on weak Wi-Fi). Tune or turn off per browser with `localStorage.setItem("homelab.playoutMs", "33")` or `"0"` in the stream frame's console, then reload. Checked offline against a 10-12-like arrival profile: one-frame hitches 38 -> 1 per 20 s at 25 ms; on a harsh Wi-Fi-like profile 49 -> 31 at 25 ms and 49 -> 1 at 50 ms. |
| `seed/webrtc-rtc.json` | `/seed` | The RTC config (`SELKIES_RTC_CONFIG_JSON`) for instances in WebRTC mode: no STUN or TURN servers. It outranks Selkies' other STUN/TURN sources, the last of which is a public third-party relay (`staticauth.openrelay.metered.ca`, with a published shared secret). Selkies trusts the file only if root owns it and it isn't group- or world-writable, as deploy installs it. |
| `seed/xemu-xbox_hdd.qcow2.zip` | `/seed` | xemu's official blank 8 GiB HDD (xemu-project/xemu-hdd-image 1.0, sha256 `d9f5a4c1…bb39`). It's extracted to `/config/xemu/xbox_hdd.qcow2` once, and the broker converts it to raw on first launch. |
| `bin/azahar-xcb` | `/opt/webstation-bin` | The `AZAHAR_BIN` launch wrapper. It forces `QT_QPA_PLATFORM=xcb` because on native Wayland Azahar's frames never reach the window on NVIDIA (the game runs with audio, but the screen stays black). |
| `bin/webrtc-public-ip` | `/opt/webstation-bin` | `webrtc-public-ip <host>` prints the public IPv4 a WebRTC instance should advertise, or exits 1 with a reason when the name doesn't resolve, isn't public, or answers with a Cloudflare proxy address. Used by `40-webrtc-public-ip` and `svc-webrtc-ip-watch`. |

Each container's `/config` (cores, BIOS copies, emulator settings, broker exports) lives
in `/home/jf/webstation-hub/config`, `config-2` or `config-3` and is not in git.

## Three instances

One webstation runs one game session, so there are three, to let three games stream at once.

| Instance | Container | `/config` | jf vCPUs | Broker (RomM, LAN) | Player path (Traefik) |
|---|---|---|---|---|---|
| 1 | `romm-webstation` | `config` | 0-3 | `http://10.0.1.8:3010` | `/streaming` |
| 2 | `romm-webstation-2` | `config-2` | 4-7 | `http://10.0.1.8:3020` | `/streaming-2` |
| 3 | `romm-webstation-3` | `config-3` | 8-11 | `http://10.0.1.8:3030` | `/streaming-3` |

Each runs on its own 4 of jf's 12 vCPUs (a cpuset, not a CFS quota) and is capped at 3 GiB
(jf has 16 GiB); all three share the 3060 Ti. The cpusets keep an emulator's hot thread from
being bounced across all 12 vCPUs, which cost xemu about 8% of its CPU time. On phoenix, jf
is pinned to the faster CCD (`qm` affinity `0-7,16-23`), which lives in the Proxmox config,
not here.
They share `BROKER_SECRET`: RomM's `STREAMING_BROKER_SECRET` env var overrides every
container's `broker_secret`, so the secrets can't differ.

RomM pools containers that run the same emulator and core for a platform (`interchangeable_with`
in RomM 5.4.0), and a launch takes the first free one in config order, so instance 1 stays warm.
When all three are busy, players see "All 3 containers for this platform are in use".
The pool is RomM's `streaming.containers` list in its live `config.yml` on ServiceHub (not in git):
three entries with identical `platforms`, differing only in `label`, `host`, `subfolder` and `broker_host`:

```yaml
- label: jf webstation 2
  protocol: webstation
  host: /streaming-2
  subfolder: /streaming-2
  broker_host: http://10.0.1.8:3020
  platforms: { ... same as instance 1 ... }
```

Same-origin pool members each need their own proxied path, which is why every instance has
its own `SUBFOLDER` and Traefik routers rather than one load-balanced path.

Changing any of these files restarts all three containers on deploy, ending any running sessions.

## WebRTC over UDP (all four, since 2026-11-07)

By default Selkies sends video, audio and input in one WebSocket, which is TCP from the
browser through Cloudflare. One lost packet holds up everything behind it until it's resent,
so players see brief freezes, audio included. In WebRTC mode only the signalling stays on that
WebSocket. The media goes over UDP, where a lost packet delays only its own frame (resent by
NACK/RTX), and audio is a separate stream sent ahead of video. An A/B on instance 1
(2026-11-07) settled it: WebRTC "5x better", websockets latency "unplayable". Decision: vault
`5-Decisions/2026-11-07_webstations-webrtc-udp.md`; design and checks:
`1-Projects/romm-emulator-streaming/notes/webrtc-udp-transport-plan.md`.

Each instance borrows an idle GamingHub UDP forward (we have no access to X-Mansion's pfSense):

| Instance | WAN UDP port | Traefik UDP router | Borrowed from |
|---|---|---|---|
| 1 | 7787 | `ark7787` | Ark Map 5 (never set up; Ark is stopped) |
| 2 | 7783 | `ark7783` | Ark Map 3 (never set up) |
| 3 | 7784 | `ark7784` | Ark Map 3's second port |
| Forge | 7788 | `ark7788` | Ark Map 5's second port |

```
player -> X-Mansion WAN :77xx/udp -> pfSense -> Traefik VM :77xx
       -> UDP router ark77xx (hosts/traefik/config/dynamic/games-udp.yml) -> 10.0.1.8:77xx -> instance
Forge: ... -> UDP router ark7788 -> studio-gw 10.0.1.150:7788 (studio_gw_ingress forge-romm-webrtc)
       -> 10.77.0.10:7788 -> romm-webstation-forge
```

The Forge's webstation sits on the studio network, whose DNS answers only `.studio` names
(studio-gw's dnsmasq has no upstream, on purpose). So it can't resolve
`webstation-udp.ninjaprivacy.org`. Instead studio-gw looks the name up itself every 5 minutes
and publishes it as `webstation-udp.studio` (`studio_gw_published_names`, timer
`studio-gw-publish-names`). The Forge sets `WEBRTC_PUBLIC_HOST=webstation-udp.studio`, and
`40-webrtc-public-ip` and `svc-webrtc-ip-watch` work unchanged.

- **Candidate address.** `SELKIES_WEBRTC_UDP_MUX_PORT` gives all of an instance's sessions one
  UDP port. `SELKIES_WEBRTC_ICE_LITE=true` makes the server offer only its host candidate,
  rewritten to the public IP from `40-webrtc-public-ip`. The mux port must equal the WAN port,
  because the candidate carries it. The public name is `webstation-udp.ninjaprivacy.org`, a
  **DNS-only** CNAME to the DDNS record `games-udp`; `svc-webrtc-ip-watch` follows it.
- **WebRTC settings live in `x-webrtc-env`,** never in the shared `x-webstation-env`. The
  image's `init-selkies-config` treats any WebRTC setting (`SELKIES_RTC_CONFIG_JSON`,
  `SELKIES_TURN_*`, `SELKIES_STUN_*`, `SELKIES_WEBRTC_PUBLIC_IP`, ...) as "WebRTC configured".
  It then sets `SELKIES_MODE=webrtc` on an instance that doesn't set its own mode, and turns
  dual mode on unless it's set.
- **Dual mode is off.** It would let any controller-role player switch an instance's transport
  for everyone on it, and the switch outlives the session.
- **No fallback** for players whose network blocks UDP: they can't use a WebRTC instance.
- **Traefik relays every media packet** in userspace on its 1-vCPU VM. One stream cost it
  about 3% (12% peak). Its UDP sessions idle out after 3 s; a hidden tab still resumed.
- **To roll back an instance:** drop `*webrtc-env`, its mux port and its `77xx/udp` port, and
  point its `ark77xx_svc` back at `10.0.1.9`.

## A fourth on the studio Forge

`iac/ansible/roles/forge_webstation` runs one more webstation from these same files on the
studio Forge (VM 155, RTX 5070 Ti), to compare emulation with jf's 3060 Ti: container
`romm-webstation-forge`, player path `/streaming-forge`, broker `http://10.0.1.150:3040`
(through studio-gw). A change to these files reaches it at the Forge's next `site.yml` run,
not through deploy-webstation. RomM sends sessions to it only while its live `config.yml`
lists it; during a comparison it goes first, so launches land there:

```yaml
- label: forge webstation
  protocol: webstation
  host: /streaming-forge
  subfolder: /streaming-forge
  broker_host: http://10.0.1.150:3040
  platforms: { ... same as instance 1 ... }
```

PCSX2 reads `patches.zip` only from `/usr/share/PCSX2/resources/`. The broker's own refresh installs it with `sudo`, which `DISABLE_SUDO=true` blocks, so compose sets `PCSX2_PATCHES_FETCH=false` and the cont-init script fetches it as root. It sits in the container layer, so a recreate fetches it again.

## Emulator performance defaults

`50-seed-emulators` adds these keys only where a file doesn't set them yet (`set_defaults`), so
a value someone later changes in the emulator's own menu stays (except xemu's, below). Found
while chasing stutter on the heavy systems (2026-10-12):

| Emulator | File | Keys | Why |
|---|---|---|---|
| Dolphin | `.config/dolphin-emu/GFX.ini` | `[Settings] ShaderCompilationMode = 2`, `ShaderPrecompilerThreads = 3`; `[Hardware] VSync = True`; `[Hacks] SkipDuplicateXFBs = False` | The empty default compiles every new shader synchronously, so a game hitches the first time an effect appears (worst on instances 2 and 3, whose caches start cold). Hybrid ubershaders compile in the background, on 3 threads rather than the 10 that auto picks from jf's 12 vCPUs. `WaitForShadersBeforeStarting` stays off: on a cold cache it would hold the game past the broker's 5 s delay before loading a resume state. VSync with duplicate frames presented should give 30 fps games a near-even cadence on the 60 Hz capture (untested; Dolphin can't sync to the host rate, so one repeat per ~17 s remains). |
| PCSX2 | `.config/PCSX2/inis/PCSX2.ini` | `[EmuCore/GS] VsyncEnable`, `SyncToHostRefreshRate`, `UseVSyncForTiming = true`; `[EmuCore/Speedhacks] vuThread = true` | Selkies captures at 60.000 Hz, and PCSX2's own 59.94 Hz limiter drifts against it. With these, PCSX2 paces on VSync, so the capture rate sets game speed: compose locks it with `SELKIES_FRAMERATE=60-60`. Check the emulog for `Refresh rate: Host=60… can sync and using vsync for pacing`. MTVU is PCSX2's own default on 3+ cores, but it applies it only to an ini it writes itself. |
| xemu | `.local/share/xemu/xemu/xemu.toml` | `[audio.vp] num_workers = 3` | Auto starts one voice worker per CPU xemu sees: all 12 of jf's vCPUs, not the 4 in the cpuset. Those 12 threads woke about 22,000 times a second on 4 vCPUs (container CPU pressure 11-15% with the vCPUs half idle). Auto can't be chosen from xemu's menu and kept: xemu drops default-valued keys on exit and the seed puts 3 back, so change it here. |

Compose also sets `memswap_limit` equal to `mem_limit`, so nothing in a webstation is swapped
out: with MGLRU, jf's swappiness of 10 didn't stop the kernel from swapping a running xemu,
Selkies and labwc while qBittorrent's page cache (and chdman ROM ingests on jf) filled memory.
It also locks `SELKIES_FRAMERATE` at 60 (see PCSX2 above; RetroArch and xemu pace on the same frames).

xemu emulates the Xbox CPU on one TCG thread; there is no multithreaded or KVM path. That thread
reads 93-97% of a vCPU in Shenmue II, but also ~97% at Halo's idle menu, so the figure doesn't show
whether a game is running below speed. In a live A/B on 2026-10-12, giving the thread its own
physical core on phoenix and then a vCPU of its own in the container (run-queue wait 39 -> 17 ms/s)
made no felt difference, so CPU contention isn't the cause. Whether Shenmue II actually drops below
30 fps here is unmeasured: read xemu's Debug > Video FPS during a slowdown before calling it a CPU
limit (these instances run `surface_scale = 2`).

Not every freeze is server-side: on 2026-10-12, brief freezes in PCSX2 (Legaia 2, EE ~35%) and
RetroArch (Smash 64) seen from a remote client never appeared on jf, whose stream stayed
continuous. Check the client and transport (Selkies client stats, a LAN test) before tuning
emulators.

Azahar (3DS) needs nothing here: the image seeds `qt-config.ini` from `/defaults/azahar.ini`, which already binds the virtual pad (GUID `030081b85e0400008e02000014010000`) through SDL's controller API.
