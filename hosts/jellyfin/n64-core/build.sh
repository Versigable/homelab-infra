#!/usr/bin/env bash
# Builds the webstations' N64 core: libretro's mupen64plus-next at $COMMIT plus
# mupen64plus-next-homelab.patch (five upstream GLideN64 fixes, see README.md), the way
# libretro's CI builds it for Linux x86_64: gcc 9, platform=unix, the same HAVE_* flags,
# stripped. The version string reads "3.0-Vulkan <commit>-homelab<N>", which is how
# custom-cont-init.d/48-n64-core-check tells this build from libretro's.
#
# Usage, on any Docker host with internet (jf): sudo ./build.sh [outdir]
# Takes about 10 minutes on 4 CPUs. Writes mupen64plus_next_libretro.so, a .zip of it,
# SHA256SUMS and build.log to outdir (default ./n64-core-out).
set -euo pipefail

COMMIT=12edd2c74a517ff86dfa8cfc71ad75e4c10486d5  # libretro/mupen64plus-libretro-nx develop, 2026-10-01
BUILD_TAG=homelab1                               # bump when the patch changes
IMAGE=ubuntu:20.04                               # gcc 9, as libretro's xenial-gcc9 builder
CPUS=${BUILD_CPUS:-4}

here=$(cd "$(dirname "$0")" && pwd)
out=$(realpath -m "${1:-$PWD/n64-core-out}")
mkdir -p "$out"

docker run --rm --cpus "$CPUS" --cpu-shares 256 \
  -e COMMIT="$COMMIT" -e BUILD_TAG="$BUILD_TAG" -e CPUS="$CPUS" \
  -e OWNER="$(stat -c %u:%g "$out")" \
  -v "$here:/in:ro" -v "$out:/out" "$IMAGE" bash -euo pipefail -c '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends \
      build-essential git ca-certificates nasm libgl1-mesa-dev zip >/dev/null
    git init -q /src && cd /src
    git fetch -q --depth 1 https://github.com/libretro/mupen64plus-libretro-nx.git "$COMMIT"
    git checkout -q FETCH_HEAD
    git apply --verbose /in/mupen64plus-next-homelab.patch
    gcc --version | head -1
    if ! make -j"$CPUS" platform=unix CC=gcc CXX=g++ \
        HAVE_PARALLEL_RDP=1 HAVE_PARALLEL_RSP=1 HAVE_THR_AL=1 LLE=1 \
        GIT_VERSION="\" ${COMMIT:0:7}-$BUILD_TAG\"" >/out/build.log 2>&1; then
      tail -40 /out/build.log; exit 1
    fi
    strip --strip-unneeded mupen64plus_next_libretro.so
    cp mupen64plus_next_libretro.so /out/
    cd /out
    rm -f mupen64plus_next_libretro.so.zip
    zip -q -9 mupen64plus_next_libretro.so.zip mupen64plus_next_libretro.so
    sha256sum mupen64plus_next_libretro.so mupen64plus_next_libretro.so.zip > SHA256SUMS
    grep -a -o -E "3\.0-[A-Za-z]+ [0-9a-f]{7}-$BUILD_TAG" mupen64plus_next_libretro.so | head -1
    chown -R "$OWNER" /out
  '
cat "$out/SHA256SUMS"
