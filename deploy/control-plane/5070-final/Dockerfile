ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG SOURCE_REVISION
ARG BASE_IMAGE_ID
ARG COMPONENT
USER root
ENV GPU_CONTROL_BUILD_VERSION=1.5.23.post3 \
    GPU_CONTROL_BUILD_REVISION=${SOURCE_REVISION} \
    GPU_CONTROL_ADAPTER_BASE_IMAGE_ID=${BASE_IMAGE_ID} \
    GPU_CONTROL_RESOURCE_COMPONENT=${COMPONENT}
COPY layer /tmp/control-layer
RUN python /tmp/control-layer/apply_control_layer.py && rm -rf /tmp/control-layer
LABEL org.opencontainers.image.version="1.5.23.post3" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      io.gpu-control.adapter.base-image-id="${BASE_IMAGE_ID}" \
      io.gpu-control.adapter.scope="terminal-batch-guard-and-codex-freshness-limits"
USER gpucontrol
