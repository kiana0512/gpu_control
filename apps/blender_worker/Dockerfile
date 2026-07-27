ARG BLENDER_BASE_IMAGE=li3d/blender-runtime:5.1.2
FROM ${BLENDER_BASE_IMAGE}
USER root
WORKDIR /app
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY apps/blender_worker/src ./apps/blender_worker/src
RUN python3 -m pip install --no-cache-dir .
RUN useradd --uid 10002 --create-home assetworker
USER assetworker
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 CUDA_VISIBLE_DEVICES=""
CMD ["python3", "-m", "gpu_control_blender_worker.main"]
