#!/usr/bin/env bash
set -Eeuo pipefail

usage(){ echo "用法: $0 [--ca deploy/control-plane/nginx/certs/lan-ca.crt|--insecure]"; }
curl_tls=()
while (($#)); do
  case "$1" in
    --ca) curl_tls=(--cacert "$2"); shift 2 ;;
    --insecure) curl_tls=(-k); shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ -f .env ]] || { echo "FAIL 缺少 /opt/gpu-control/.env" >&2; exit 1; }
set -a
source .env
set +a

failed=0
check_url() {
  local label="$1" url="$2"
  if curl "${curl_tls[@]}" --connect-timeout 3 --max-time 8 -fsS "${url}" >/dev/null; then
    printf 'OK   %-24s %s\n' "${label}" "${url}"
  else
    printf 'FAIL %-24s %s\n' "${label}" "${url}" >&2
    failed=1
  fi
}

if [[ "${GPU_CONTROL_ROLE:-control}" == "node" ]]; then
  check_url "local ComfyUI" "http://${NODE_BIND_IP}:8188/system_stats"
  check_url "control Loki" "http://${CONTROL_HOST}:3100/ready"
else
  check_url "public API" "${PUBLIC_BASE_URL}/health/ready"
  check_url "central Loki" "http://${CONTROL_HOST}:3100/ready"
  for item in "3090-A:${WORKER_3090_A_HOST}" "3090-B:${WORKER_3090_B_HOST}"; do
    label="${item%%:*}"
    host="${item#*:}"
    check_url "${label} ComfyUI" "http://${host}:8188/system_stats"
    check_url "${label} Agent" "http://${host}:9201/health/ready"
    check_url "${label} node metrics" "http://${host}:9100/metrics"
    if [[ "${label}" == "3090-B" ]]; then
      echo "SKIP ${label} DCGM metrics       WSL 节点由 Node Agent 上报 GPU 指标"
    else
      check_url "${label} GPU metrics" "http://${host}:9400/metrics"
    fi
  done
  if docker compose -f deploy/control-plane/compose.yaml exec -T postgres \
      pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" >/dev/null; then
    echo "OK   PostgreSQL"
  else
    echo "FAIL PostgreSQL" >&2; failed=1
  fi
  if docker compose -f deploy/control-plane/compose.yaml exec -T redis \
      redis-cli -a "${REDIS_PASSWORD}" --no-auth-warning ping | grep -q PONG; then
    echo "OK   Redis"
  else
    echo "FAIL Redis" >&2; failed=1
  fi
fi

((failed == 0)) || { echo "联通检查失败，请按输出检查 UFW、IP 和容器状态。" >&2; exit 1; }
echo "联通检查全部通过。"
