#!/usr/bin/env bash
set -Eeuo pipefail

usage(){ echo "用法: sudo $0 --role control|node"; }
role=""
while (($#)); do
  case "$1" in
    --role) role="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ "${EUID}" -eq 0 && "${role}" =~ ^(control|node)$ ]] || { usage >&2; exit 2; }
readonly root="/opt/gpu-control"
[[ -f "${root}/.env" ]] || { echo "缺少 ${root}/.env" >&2; exit 1; }

set -a
source "${root}/.env"
set +a
[[ -n "${NODE_AGENT_HMAC_SECRET:-}" && "${NODE_AGENT_HMAC_SECRET}" != CHANGE_ME* ]] || {
  echo "请先在 .env 设置 NODE_AGENT_HMAC_SECRET" >&2
  exit 1
}
if [[ "${role}" == node ]]; then
  [[ -n "${NODE_ID:-}" && -n "${CONTROL_HOST:-}" ]] || {
    echo "节点角色必须配置 NODE_ID 和 CONTROL_HOST" >&2
    exit 1
  }
  [[ -r "${NODE_CONTROL_CA_CERT:-/etc/gpu-control/lan-ca.crt}" ]] || {
    echo "缺少控制中心 CA：${NODE_CONTROL_CA_CERT:-/etc/gpu-control/lan-ca.crt}" >&2
    exit 1
  }
fi

id gpuagent >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin gpuagent
python3 -m venv "${root}/.venv"
"${root}/.venv/bin/pip" install \
  --disable-pip-version-check \
  --no-cache-dir \
  "fastapi==0.116.1" \
  "PyJWT==2.10.1" \
  "argon2-cffi==25.1.0" \
  "pydantic==2.11.7" \
  "pydantic-settings==2.10.1" \
  "structlog==25.4.0" \
  "uvicorn[standard]==0.35.0"
"${root}/.venv/bin/pip" install \
  --disable-pip-version-check \
  --ignore-requires-python \
  --no-cache-dir \
  --no-deps \
  "${root}"

install -d -m 0755 /etc/gpu-control
if [[ "${role}" == control ]]; then
  install -m 0644 \
    "${root}/deploy/control-plane/nginx/certs/lan-ca.crt" \
    /etc/gpu-control/lan-ca.crt
fi
install -m 0755 "${root}/scripts/gpu-node-ctl" /usr/local/sbin/gpu-node-ctl
install -m 0644 "${root}/apps/node_agent/systemd/gpu-node-agent.service" /etc/systemd/system/gpu-node-agent.service
if command -v nvidia-smi >/dev/null; then
  install -m 0644 \
    "${root}/apps/node_agent/systemd/gpu-control-nvidia-persistence.service" \
    /etc/systemd/system/gpu-control-nvidia-persistence.service
fi
install -m 0440 "${root}/apps/node_agent/systemd/gpu-node-agent.sudoers" /etc/sudoers.d/gpu-node-agent
visudo -cf /etc/sudoers.d/gpu-node-agent
printf 'role=%s\n' "${role}" > /etc/gpu-control/node-role
chmod 0644 /etc/gpu-control/node-role
{
  printf 'ENVIRONMENT=production\n'
  printf 'NODE_AGENT_HOST=0.0.0.0\n'
  printf 'NODE_AGENT_PORT=%s\n' "${NODE_AGENT_PORT:-9201}"
  printf 'NODE_AGENT_HMAC_SECRET=%s\n' "${NODE_AGENT_HMAC_SECRET}"
  printf 'NODE_ID=%s\n' "${NODE_ID:-}"
  printf 'CONTROL_HOST=%s\n' "${CONTROL_HOST:-}"
  printf 'NODE_ADVERTISE_IP=%s\n' "${NODE_ADVERTISE_IP:-}"
  printf 'NODE_HEARTBEAT_INTERVAL_SECONDS=%s\n' "${NODE_HEARTBEAT_INTERVAL_SECONDS:-10}"
  printf 'NODE_CONTROL_CA_CERT=%s\n' "${NODE_CONTROL_CA_CERT:-/etc/gpu-control/lan-ca.crt}"
} > /etc/gpu-control/node-agent.env
chmod 0600 /etc/gpu-control/node-agent.env

systemctl daemon-reload
if command -v nvidia-smi >/dev/null; then
  systemctl enable --now gpu-control-nvidia-persistence.service
fi
systemctl enable gpu-node-agent
systemctl restart gpu-node-agent
systemctl --no-pager --full status gpu-node-agent
