# Studio transcribe image: the play image (for its ffmpeg) plus whisper.cpp's command-line
# tool, which writes down what a player said in a session's voice file. forge-playd runs it
# with no network and no capabilities, on the CPU, after the session has ended.
# Built on the Forge by Ansible (role forge_play) from a source tarball the control node
# downloaded and checked; the Forge reaches nothing but Debian's packages. The tag is a hash
# of this file, the play image's tag and the tarball's checksum.
# The build tunes itself to the Forge's CPU (whisper.cpp's default), and it runs there only.
ARG BASE
FROM ${BASE} AS build

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential cmake \
 && rm -rf /var/lib/apt/lists/*

COPY whisper.cpp.tar.gz /tmp/whisper.cpp.tar.gz
RUN mkdir /src \
 && tar -xzf /tmp/whisper.cpp.tar.gz -C /src --strip-components=1 \
 && cmake -S /src -B /src/build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
      -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF \
 && cmake --build /src/build -j 8 --target whisper-cli

FROM ${BASE}
COPY --from=build /src/build/bin/whisper-cli /usr/local/bin/whisper-cli
