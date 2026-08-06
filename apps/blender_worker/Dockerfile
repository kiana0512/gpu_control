ARG BLENDER_BASE_IMAGE=li3d/blender-runtime:5.1.2
FROM ${BLENDER_BASE_IMAGE}
ARG ASSET_WORKER_VERSION=1.4.2-retopology-v2.3.0
ARG GPU_CONTROL_REVISION=unknown
USER root
WORKDIR /app
RUN apt-get update -qq \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
        bubblewrap \
        xvfb \
        xauth \
    && rm -rf /var/lib/apt/lists/*

# Codex is mounted at runtime as one multi-call binary.  Workspace-write
# sandbox execution invokes the same binary through this companion argv[0].
RUN ln -s /usr/local/bin/codex /usr/local/bin/codex-linux-sandbox
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps/blender_worker/src ./apps/blender_worker/src
COPY scripts/probe_retopoflow_blender.py ./scripts/probe_retopoflow_blender.py
COPY resources/retopology-v6 /opt/li3d/retopology-v6
COPY resources/retopology-direct-v2 /opt/li3d/retopology-direct-v2
RUN python3 /opt/li3d/retopology-direct-v2/server/verify_package.py
RUN python3 -m pip install --no-cache-dir .
RUN python3 -c "from pathlib import Path; from packages.gpu_control_core.retopology_v6 import verify_runtime_resources; verify_runtime_resources(Path('/opt/li3d/retopology-v6'))"
RUN useradd --uid 10002 --create-home assetworker
USER assetworker
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CUDA_VISIBLE_DEVICES="" \
    ASSET_WORKER_BUILD_VERSION=${ASSET_WORKER_VERSION} \
    GPU_CONTROL_BUILD_REVISION=${GPU_CONTROL_REVISION}
LABEL org.opencontainers.image.title="GPU Control Blender Worker" \
      org.opencontainers.image.version="${ASSET_WORKER_VERSION}" \
      org.opencontainers.image.revision="${GPU_CONTROL_REVISION}" \
      org.opencontainers.image.source="https://github.com/kiana0512/gpu_control.git"
ENTRYPOINT ["python3", "-m", "gpu_control_blender_worker.bootstrap"]
CMD ["python3", "-m", "gpu_control_blender_worker.main"]
