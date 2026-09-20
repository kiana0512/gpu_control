FROM python:3.11.13-slim-bookworm

ARG GPU_CONTROL_VERSION=1.5.23
ARG GPU_CONTROL_REVISION=unknown
LABEL org.opencontainers.image.title="GPU Control AutoDL Comfy tunnel" \
      org.opencontainers.image.version="${GPU_CONTROL_VERSION}" \
      org.opencontainers.image.revision="${GPU_CONTROL_REVISION}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 gpucontrol \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin gpucontrol

COPY apps/autodl_tunnel/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY --chmod=0555 scripts/autodl_comfy_tunnel.py /usr/local/bin/autodl-comfy-tunnel
COPY --chmod=0444 scripts/autodl_remote_start_comfyui.sh /usr/local/share/autodl_remote_start_comfyui.sh

USER gpucontrol:gpucontrol
EXPOSE 16006
HEALTHCHECK --interval=15s --timeout=5s --retries=4 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18080/health/live',timeout=3)"]
ENTRYPOINT ["python", "/usr/local/bin/autodl-comfy-tunnel"]
