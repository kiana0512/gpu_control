FROM python:3.11.13-slim-bookworm AS builder
WORKDIR /app
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps/asset_api/src ./apps/asset_api/src
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir .

FROM python:3.11.13-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH
RUN groupadd --gid 10001 gpucontrol && useradd --uid 10001 --gid gpucontrol --create-home gpucontrol
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
USER gpucontrol
EXPOSE 8010
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/health/live', timeout=2)"
CMD ["uvicorn", "gpu_control_asset_api.main:app", "--host", "0.0.0.0", "--port", "8010", "--proxy-headers", "--forwarded-allow-ips=*"]
