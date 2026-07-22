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

id gpuagent >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin gpuagent
python3 -m venv "${root}/.venv"
"${root}/.venv/bin/pip" install --disable-pip-version-check --no-cache-dir "${root}"

install -d -m 0755 /etc/gpu-control
install -m 0755 "${root}/scripts/gpu-node-ctl" /usr/local/sbin/gpu-node-ctl
install -m 0644 "${root}/apps/node_agent/systemd/gpu-node-agent.service" /etc/systemd/system/gpu-node-agent.service
install -m 0440 "${root}/apps/node_agent/systemd/gpu-node-agent.sudoers" /etc/sudoers.d/gpu-node-agent
visudo -cf /etc/sudoers.d/gpu-node-agent
printf 'role=%s\n' "${role}" > /etc/gpu-control/node-role
chmod 0644 /etc/gpu-control/node-role
{
  printf 'ENVIRONMENT=production\n'
  printf 'NODE_AGENT_HOST=0.0.0.0\n'
  printf 'NODE_AGENT_PORT=%s\n' "${NODE_AGENT_PORT:-9201}"
  printf 'NODE_AGENT_HMAC_SECRET=%s\n' "${NODE_AGENT_HMAC_SECRET}"
} > /etc/gpu-control/node-agent.env
chmod 0600 /etc/gpu-control/node-agent.env

systemctl daemon-reload
systemctl enable --now gpu-node-agent
systemctl --no-pager --full status gpu-node-agent
