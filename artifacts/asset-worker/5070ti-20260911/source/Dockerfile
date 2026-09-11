FROM li3d/blender-worker:1.4.75-uv-overlap-v28-pins1
USER root
COPY --chown=root:root patched/main.py /usr/local/lib/python3.11/site-packages/gpu_control_blender_worker/main.py
USER assetworker
ENV ASSET_WORKER_BUILD_VERSION=1.4.75-uv-overlap-v28-pins1-codex-auth1
LABEL org.opencontainers.image.version="1.4.75-uv-overlap-v28-pins1-codex-auth1"
