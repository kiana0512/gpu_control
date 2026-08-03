ARG BLENDER_BASE_IMAGE=li3d/blender-runtime:5.1.2
FROM ${BLENDER_BASE_IMAGE}
ARG ASSET_WORKER_VERSION=1.2.5
ARG GPU_CONTROL_REVISION=unknown
USER root
WORKDIR /app
RUN apt-get update -qq \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends xvfb xauth \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps/blender_worker/src ./apps/blender_worker/src
COPY scripts/probe_retopoflow_blender.py ./scripts/probe_retopoflow_blender.py
RUN python3 -m pip install --no-cache-dir .
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
