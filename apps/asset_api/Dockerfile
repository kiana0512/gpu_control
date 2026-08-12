ARG PYTHON_BASE_IMAGE=python:3.11.13-slim-bookworm
FROM ${PYTHON_BASE_IMAGE} AS builder
WORKDIR /app
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps/asset_api/src ./apps/asset_api/src
COPY resources/retopology-v6 /opt/li3d/retopology-v6
RUN python -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --no-cache-dir --upgrade pip==26.2.1 \
    && /opt/venv/bin/python -m pip install --no-cache-dir .

FROM ${PYTHON_BASE_IMAGE} AS runtime
ARG GPU_CONTROL_VERSION=1.6.39-retopo-trained-skill-v1
ARG GPU_CONTROL_REVISION=unknown
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH
ENV GPU_CONTROL_BUILD_VERSION=${GPU_CONTROL_VERSION} \
    GPU_CONTROL_BUILD_REVISION=${GPU_CONTROL_REVISION}
LABEL org.opencontainers.image.title="GPU Control Asset API" \
      org.opencontainers.image.version="${GPU_CONTROL_VERSION}" \
      org.opencontainers.image.revision="${GPU_CONTROL_REVISION}" \
      org.opencontainers.image.source="https://github.com/kiana0512/gpu_control.git"
RUN python -m pip install --no-cache-dir --upgrade pip==26.2.1 \
    && groupadd --gid 10001 gpucontrol \
    && useradd --uid 10001 --gid gpucontrol --create-home gpucontrol
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/li3d/retopology-v6 /opt/li3d/retopology-v6
USER gpucontrol
EXPOSE 8010
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health/live', timeout=2)"
CMD ["uvicorn", "gpu_control_asset_api.main:app", "--host", "0.0.0.0", "--port", "8010", "--proxy-headers", "--forwarded-allow-ips=*"]
