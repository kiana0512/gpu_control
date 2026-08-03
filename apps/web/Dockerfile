ARG NODE_BASE_IMAGE=node:22.17.1-alpine3.22
ARG NGINX_BASE_IMAGE=nginx:1.28.0-alpine
FROM ${NODE_BASE_IMAGE} AS builder
WORKDIR /app
ARG VITE_MODELVIEW_PROMPT_ENABLED=false
ARG GPU_CONTROL_VERSION=1.5.7
ARG GPU_CONTROL_REVISION=unknown
ENV VITE_MODELVIEW_PROMPT_ENABLED=${VITE_MODELVIEW_PROMPT_ENABLED} \
    VITE_GPU_CONTROL_VERSION=${GPU_CONTROL_VERSION} \
    VITE_GPU_CONTROL_REVISION=${GPU_CONTROL_REVISION}
COPY package*.json ./
RUN npm ci --ignore-scripts && npm cache clean --force
COPY . .
RUN npm run build
FROM ${NGINX_BASE_IMAGE}
ARG GPU_CONTROL_VERSION=1.5.7
ARG GPU_CONTROL_REVISION=unknown
LABEL org.opencontainers.image.title="GPU Control Web" \
      org.opencontainers.image.version="${GPU_CONTROL_VERSION}" \
      org.opencontainers.image.revision="${GPU_CONTROL_REVISION}" \
      org.opencontainers.image.source="https://github.com/kiana0512/gpu_control.git"
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD wget -qO- http://127.0.0.1/
