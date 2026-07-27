#!/usr/bin/env bash
set -Eeuo pipefail

image="${COMFY_IMAGE:-gpu-control/comfyui:projects-0.2.3}"
container="${COMFY_CONTAINER:-comfyui-4090}"
bind_ip="${COMFY_BIND_IP:-0.0.0.0}"
port="${COMFY_PORT:-8188}"
gpu="${COMFY_GPU:-all}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
imageclip_root="${IMAGECLIP_ROOT:-/opt/imageclip}"
modelview_root="${MODELVIEW_ROOT:-/opt/modelviewcreator}"
model_root="${MODEL_ROOT:-${imageclip_root}/models}"
data_root="${COMFY_DATA_ROOT:-/srv/comfyui/4090}"

usage() {
  cat <<'EOF'
用法: scripts/comfyui-server.sh start|stop|restart|status|logs|shell

可选环境变量：
  COMFY_IMAGE       镜像名，默认 gpu-control/comfyui:projects-0.2.3
  COMFY_CONTAINER   容器名，默认 comfyui-4090
  COMFY_BIND_IP     监听地址，默认 0.0.0.0
  COMFY_PORT        宿主端口，默认 8188
  COMFY_GPU         Docker GPU 选择，默认 all
  MODEL_ROOT        模型目录，默认 /opt/imageclip/models
  COMFY_DATA_ROOT   数据目录，默认 /srv/comfyui/4090
  IMAGECLIP_ROOT    ImageClip Git 仓库，默认 /opt/imageclip
  MODELVIEW_ROOT    ModelViewCreator Git/LFS 仓库，默认 /opt/modelviewcreator
EOF
}

container_exists() {
  docker container inspect "${container}" >/dev/null 2>&1
}

container_running() {
  [[ "$(docker container inspect --format '{{.State.Running}}' "${container}" 2>/dev/null || true)" == true ]]
}

prepare_directories() {
  [[ -f "${imageclip_root}/ImageClip.json" ]] || {
    echo "缺少 ImageClip 仓库: ${imageclip_root}" >&2
    return 1
  }
  [[ -f "${imageclip_root}/Cherry_lizi/__init__.py" ]] || {
    echo "缺少 Cherry_lizi 节点: ${imageclip_root}/Cherry_lizi" >&2
    return 1
  }
  [[ -f "${modelview_root}/flux_fill_inpaint.json" ]] || {
    echo "缺少 ModelViewCreator 工作流: ${modelview_root}/flux_fill_inpaint.json" >&2
    return 1
  }
  [[ -f "${modelview_root}/custom_nodes/haoze-LiClick/__init__.py" ]] || {
    echo "缺少 haoze-LiClick 节点: ${modelview_root}/custom_nodes/haoze-LiClick" >&2
    return 1
  }
  [[ -d "${modelview_root}/model" ]] || {
    echo "缺少 ModelViewCreator 模型目录: ${modelview_root}/model" >&2
    return 1
  }
  [[ -f "${root}/configs/comfyui-extra-model-paths.yaml" ]] || {
    echo "缺少双项目模型映射配置" >&2
    return 1
  }
  [[ -d "${model_root}" ]] || sudo install -d -m 0755 "${model_root}"
  # Nunchaku 的可选节点会在导入阶段检查这些标准模型目录。模型根目录
  # 以只读方式挂载，因此必须先在宿主机创建空目录，避免容器启动时写入失败。
  for directory in checkpoints clip embeddings ipadapter pulid insightface facexlib; do
    sudo install -d -m 0755 "${model_root}/${directory}"
  done
  for directory in input output temp user; do
    sudo install -d -m 0755 -o 10001 -g 10001 "${data_root}/${directory}"
  done
  sudo install -d -m 0755 -o 10001 -g 10001 "${data_root}/user/default/workflows"
}

wait_until_ready() {
  for _ in $(seq 1 90); do
    if docker exec "${container}" python3.11 /usr/local/bin/comfy-healthcheck >/dev/null 2>&1; then
      echo "ComfyUI 已就绪: http://$(hostname -I | awk '{print $1}'):${port}"
      return 0
    fi
    sleep 2
  done
  echo "ComfyUI 未在预期时间内就绪，请运行: $0 logs" >&2
  return 1
}

connect_control_network() {
  if docker network inspect gpu-control_backend >/dev/null 2>&1; then
    docker network connect --alias comfyui-4090 gpu-control_backend "${container}" \
      >/dev/null 2>&1 || true
  fi
}

start_server() {
  prepare_directories
  docker image inspect "${image}" >/dev/null
  if container_exists; then
    if container_running; then
      echo "${container} 已经在运行"
    else
      docker start "${container}" >/dev/null
    fi
  else
    docker run -d \
      --name "${container}" \
      --restart unless-stopped \
      --gpus "${gpu}" \
      --shm-size 8g \
      -p "${bind_ip}:${port}:8188" \
      -v "${model_root}:/opt/comfyui/models:ro" \
      -v "${modelview_root}/model:/opt/modelviewcreator/model:ro" \
      -v "${root}/configs/comfyui-extra-model-paths.yaml:/opt/comfyui/extra_model_paths.yaml:ro" \
      -v "${imageclip_root}/Cherry_lizi:/opt/comfyui/custom_nodes/Cherry_lizi:ro" \
      -v "${modelview_root}/custom_nodes/haoze-LiClick:/opt/comfyui/custom_nodes/haoze-LiClick:ro" \
      -v "${data_root}/input:/opt/comfyui/input" \
      -v "${data_root}/output:/opt/comfyui/output" \
      -v "${data_root}/temp:/opt/comfyui/temp" \
      -v "${data_root}/user:/opt/comfyui/user" \
      -v "${imageclip_root}/ImageClip.json:/opt/comfyui/user/default/workflows/ImageClip.json:ro" \
      -v "${modelview_root}/flux_fill_inpaint.json:/opt/comfyui/user/default/workflows/ModelViewCreator_flux_fill_inpaint.json:ro" \
      "${image}" >/dev/null
  fi
  connect_control_network
  wait_until_ready
}

case "${1:-}" in
  start) start_server ;;
  stop) docker stop "${container}" ;;
  restart) docker restart "${container}" >/dev/null; wait_until_ready ;;
  status)
    docker ps -a --filter "name=^/${container}$" \
      --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
    ;;
  logs) docker logs --tail 200 -f "${container}" ;;
  shell) docker exec -it "${container}" /bin/bash ;;
  -h|--help|'') usage ;;
  *) usage >&2; exit 2 ;;
esac
