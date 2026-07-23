ARG CUDA_IMAGE=nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04
FROM ${CUDA_IMAGE} AS python-builder
ARG DEBIAN_FRONTEND=noninteractive
ARG PYTHON_VERSION=3.11.13
RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates curl git libbz2-dev libffi-dev liblzma-dev libncurses-dev libreadline-dev libsqlite3-dev libssl-dev tk-dev uuid-dev xz-utils zlib1g-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /build
RUN curl --fail --show-error --location --retry 3 "https://www.python.org/ftp/python/${PYTHON_VERSION}/Python-${PYTHON_VERSION}.tgz" -o python.tgz && tar -xzf python.tgz && cd "Python-${PYTHON_VERSION}" && ./configure --prefix=/opt/python --enable-optimizations --with-lto --with-ensurepip=install && make -j"$(nproc)" && make install

FROM ${CUDA_IMAGE} AS comfy-builder
ARG DEBIAN_FRONTEND=noninteractive
ARG COMFYUI_REPOSITORY=https://github.com/Comfy-Org/ComfyUI.git
ARG COMFYUI_COMMIT=700821e1364eaab0e8f21c538a2131719fec57bf
COPY --from=python-builder /opt/python /opt/python
ENV PATH=/opt/python/bin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends build-essential ca-certificates git libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
WORKDIR /opt
RUN git clone --filter=blob:none --no-checkout "${COMFYUI_REPOSITORY}" comfyui && cd comfyui && git checkout --detach "${COMFYUI_COMMIT}" && test "$(git rev-parse HEAD)" = "${COMFYUI_COMMIT}"
COPY docker/comfyui/requirements.lock.txt /tmp/requirements.lock.txt
RUN python3.11 -m pip install --no-cache-dir --requirement /tmp/requirements.lock.txt
COPY docker/comfyui/ /tmp/comfyui-build/
RUN python3.11 /tmp/comfyui-build/install_custom_nodes.py /tmp/comfyui-build/custom_nodes.lock.yaml /opt/comfyui/custom_nodes

FROM ${CUDA_IMAGE} AS runtime
ARG BUILD_DATE=unknown
ARG VCS_REF=unknown
ARG COMFYUI_COMMIT=700821e1364eaab0e8f21c538a2131719fec57bf
ARG LOCK_SHA256=unknown
LABEL org.opencontainers.image.title="GPU Control ComfyUI" org.opencontainers.image.created="${BUILD_DATE}" org.opencontainers.image.revision="${VCS_REF}" io.gpu-control.comfyui-commit="${COMFYUI_COMMIT}" io.gpu-control.lock-sha256="${LOCK_SHA256}"
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates gcc libc6-dev libgl1 libglib2.0-0 tini && rm -rf /var/lib/apt/lists/* && groupadd --gid 10001 comfy && useradd --uid 10001 --gid comfy --home-dir /opt/comfyui comfy
COPY --from=comfy-builder /opt/python /opt/python
COPY --from=comfy-builder --chown=comfy:comfy /opt/comfyui /opt/comfyui
COPY --chown=comfy:comfy docker/comfyui/entrypoint.sh /usr/local/bin/comfy-entrypoint
COPY docker/comfyui/healthcheck.py /usr/local/bin/comfy-healthcheck
ENV PATH=/opt/python/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /opt/comfyui
RUN mkdir -p input output temp user models && chown -R comfy:comfy input output temp user models
USER comfy
EXPOSE 8188
VOLUME ["/opt/comfyui/models", "/opt/comfyui/input", "/opt/comfyui/output", "/opt/comfyui/temp", "/opt/comfyui/user"]
HEALTHCHECK --interval=20s --timeout=5s --start-period=120s --retries=6 CMD ["python3.11", "/usr/local/bin/comfy-healthcheck"]
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/comfy-entrypoint"]
