# Studio play image: the Selkies base plus what a Unity Linux player loads. The game itself
# is mounted read-only at /game; the session's autostart (mounted per game) launches it.
# Built on the Forge by Ansible (role forge_play); the tag is a hash of this file and BASE.
ARG BASE
FROM ${BASE}

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libasound2t64 \
      libegl1 \
      libgl1 \
      libgtk-3-0t64 \
      libpulse0 \
      libudev1 \
      libvulkan1 \
      libxcursor1 \
      libxi6 \
      libxinerama1 \
      libxkbcommon0 \
      libxrandr2 \
      libxss1 \
 && rm -rf /var/lib/apt/lists/*

# Wayland session (labwc); X11 apps such as the Unity player run under XWayland.
ENV PIXELFLUX_WAYLAND=true
