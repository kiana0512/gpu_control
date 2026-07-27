FROM node:22.17.1-alpine3.22 AS builder
WORKDIR /app
ARG VITE_MODELVIEW_PROMPT_ENABLED=false
ENV VITE_MODELVIEW_PROMPT_ENABLED=${VITE_MODELVIEW_PROMPT_ENABLED}
COPY package*.json ./
RUN npm ci --ignore-scripts && npm cache clean --force
COPY . .
RUN npm run build
FROM nginx:1.28.0-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD wget -qO- http://127.0.0.1/
