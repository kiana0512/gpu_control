ARG PYTHON_BASE_IMAGE=python:3.11.13-slim-bookworm
FROM ${PYTHON_BASE_IMAGE} AS builder
WORKDIR /app
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps/scheduler/src ./apps/scheduler/src
RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --upgrade pip==26.2.1 \
    && /opt/venv/bin/python -m pip install --no-cache-dir .

FROM ${PYTHON_BASE_IMAGE} AS runtime
ARG GPU_CONTROL_VERSION=1.5.11
ARG GPU_CONTROL_REVISION=unknown
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH
ENV GPU_CONTROL_BUILD_VERSION=${GPU_CONTROL_VERSION} \
    GPU_CONTROL_BUILD_REVISION=${GPU_CONTROL_REVISION}
LABEL org.opencontainers.image.title="GPU Control Scheduler" \
      org.opencontainers.image.version="${GPU_CONTROL_VERSION}" \
      org.opencontainers.image.revision="${GPU_CONTROL_REVISION}" \
      org.opencontainers.image.source="https://github.com/kiana0512/gpu_control.git"
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.1 \
    && groupadd --gid 10001 gpucontrol \
    && useradd --uid 10001 --gid gpucontrol --create-home gpucontrol
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
USER gpucontrol
EXPOSE 9108
CMD ["python", "-m", "gpu_control_scheduler.main"]
