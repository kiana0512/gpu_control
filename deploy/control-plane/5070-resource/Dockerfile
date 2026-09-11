ARG BASE_IMAGE
FROM ${BASE_IMAGE}
ARG SOURCE_REVISION
ARG COMPONENT
USER root
ENV GPU_CONTROL_BUILD_VERSION=1.5.23.post2 \
    GPU_CONTROL_BUILD_REVISION=${SOURCE_REVISION} \
    GPU_CONTROL_RESOURCE_COMPONENT=${COMPONENT}
COPY layer /tmp/resource-layer
RUN python /tmp/resource-layer/apply_resource_layer.py && rm -rf /tmp/resource-layer
LABEL org.opencontainers.image.version="1.5.23.post2" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      io.gpu-control.adapter.scope="node-hmac-and-verified-workflow-capacity"
USER gpucontrol
