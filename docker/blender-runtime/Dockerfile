FROM python:3.11.13-slim-bookworm

ARG BLENDER_URL="https://cdn.builder.blender.org/download/daily/blender-5.1.2-stable%2Bv51.ec6e62d40fa9-linux.x86_64-release.tar.xz"
ARG BLENDER_SHA256="21a6ab66b2a8b9f035fdb39c6445cdbe91e2fe1dcff30786148b757df7f9a9c5"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates curl xz-utils \
       libdbus-1-3 libegl1 libgl1 libice6 libsm6 libwayland-client0 \
       libx11-6 libxext6 libxfixes3 libxi6 libxkbcommon0 libxrender1 libxxf86vm1 \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/blender \
    && curl --fail --location --retry 5 --output /tmp/blender.tar.xz "$BLENDER_URL" \
    && echo "$BLENDER_SHA256  /tmp/blender.tar.xz" | sha256sum --check --strict \
    && tar --extract --xz --file /tmp/blender.tar.xz --directory /opt/blender --strip-components=1 \
    && rm /tmp/blender.tar.xz \
    && /opt/blender/blender --background --version | grep -F "Blender 5.1.2"

ENV BLENDER_BINARY=/opt/blender/blender CUDA_VISIBLE_DEVICES=""
LABEL org.opencontainers.image.title="LI3d Blender Runtime" \
      org.opencontainers.image.version="5.1.2" \
      org.opencontainers.image.source="https://builder.blender.org/" \
      li3d.blender.archive.sha256="21a6ab66b2a8b9f035fdb39c6445cdbe91e2fe1dcff30786148b757df7f9a9c5"
