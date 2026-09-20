ARG PYTHON_BASE_IMAGE=python:3.11.13-slim-bookworm
FROM ${PYTHON_BASE_IMAGE} AS builder
WORKDIR /app
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps/provider_controller/src ./apps/provider_controller/src
RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --upgrade pip==26.2.1 \
    && /opt/venv/bin/python -m pip install --no-cache-dir .

FROM ${PYTHON_BASE_IMAGE} AS runtime
ARG GPU_CONTROL_VERSION=1.5.23
ARG GPU_CONTROL_REVISION=unknown
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH \
    GPU_CONTROL_BUILD_VERSION=${GPU_CONTROL_VERSION} \
    GPU_CONTROL_BUILD_REVISION=${GPU_CONTROL_REVISION}
LABEL org.opencontainers.image.title="GPU Control Provider Controller" \
      org.opencontainers.image.source="https://github.com/kiana0512/gpu_control.git" \
      org.opencontainers.image.version="${GPU_CONTROL_VERSION}" \
      org.opencontainers.image.revision="${GPU_CONTROL_REVISION}"
RUN groupadd --gid 10001 gpucontrol \
    && useradd --uid 10001 --gid gpucontrol --create-home gpucontrol
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
USER gpucontrol
EXPOSE 8020
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8020/health/ready', timeout=2)"
CMD ["uvicorn", "gpu_control_provider_controller.main:app", "--host", "0.0.0.0", "--port", "8020"]
