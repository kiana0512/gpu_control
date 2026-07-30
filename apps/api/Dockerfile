ARG PYTHON_BASE_IMAGE=python:3.11.13-slim-bookworm
FROM ${PYTHON_BASE_IMAGE} AS builder
WORKDIR /app
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps/api/src ./apps/api/src
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts/bootstrap_admin.py ./scripts/bootstrap_admin.py
COPY scripts/bootstrap_nodes.py ./scripts/bootstrap_nodes.py
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir .

FROM ${PYTHON_BASE_IMAGE} AS runtime
ARG GPU_CONTROL_VERSION=1.5.5
ARG GPU_CONTROL_REVISION=unknown
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH
ENV GPU_CONTROL_BUILD_VERSION=${GPU_CONTROL_VERSION} \
    GPU_CONTROL_BUILD_REVISION=${GPU_CONTROL_REVISION}
LABEL org.opencontainers.image.title="GPU Control API" \
      org.opencontainers.image.version="${GPU_CONTROL_VERSION}" \
      org.opencontainers.image.revision="${GPU_CONTROL_REVISION}" \
      org.opencontainers.image.source="https://github.com/kiana0512/gpu_control.git"
RUN groupadd --gid 10001 gpucontrol && useradd --uid 10001 --gid gpucontrol --create-home gpucontrol
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app/alembic.ini ./alembic.ini
COPY --from=builder /app/migrations ./migrations
COPY --from=builder /app/scripts ./scripts
USER gpucontrol
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"
CMD ["uvicorn", "gpu_control_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
