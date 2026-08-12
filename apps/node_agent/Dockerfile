FROM python:3.11.13-slim-bookworm

WORKDIR /app
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps/node_agent/src ./apps/node_agent/src
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.1 \
    && python -m pip install --no-cache-dir .

ARG GPU_CONTROL_VERSION=1.5.12
ARG GPU_CONTROL_REVISION=unknown
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GPU_CONTROL_REVISION=${GPU_CONTROL_REVISION}
LABEL org.opencontainers.image.title="GPU Control Node Agent" \
      org.opencontainers.image.version="${GPU_CONTROL_VERSION}" \
      org.opencontainers.image.revision="${GPU_CONTROL_REVISION}" \
      org.opencontainers.image.source="https://github.com/kiana0512/gpu_control.git"

ENTRYPOINT ["gpu-node-agent"]
