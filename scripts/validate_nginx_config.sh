#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(cd -- "${script_dir}/.." && pwd)"
nginx_config="${repository_root}/deploy/control-plane/nginx/nginx.conf"
nginx_image="${NGINX_CONFIG_TEST_IMAGE:-nginx:1.28.0-alpine}"
temporary_cert_dir="$(mktemp -d "${TMPDIR:-/tmp}/gpu-control-nginx-test.XXXXXX")"

cleanup() {
  rm -rf -- "${temporary_cert_dir}"
}
trap cleanup EXIT

# nginx -t loads the TLS material even though it opens no listener. Generate a
# disposable certificate so validation never depends on production secrets.
openssl req \
  -x509 \
  -newkey rsa:2048 \
  -nodes \
  -days 1 \
  -subj "/CN=gpu-control-nginx-config-test" \
  -keyout "${temporary_cert_dir}/server.key" \
  -out "${temporary_cert_dir}/server.crt" \
  >/dev/null 2>&1
cp "${temporary_cert_dir}/server.crt" "${temporary_cert_dir}/lan-ca.crt"

docker run --rm --network none \
  --mount "type=bind,src=${nginx_config},dst=/etc/nginx/nginx.conf,readonly" \
  --mount "type=bind,src=${temporary_cert_dir},dst=/etc/nginx/certs,readonly" \
  "${nginx_image}" \
  nginx -t -c "/etc/nginx/nginx.conf"
