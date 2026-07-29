#!/usr/bin/env bash
set -Eeuo pipefail
usage() {
  echo "用法: $0 --host HOST [--user USER] [--port PORT] [--known-hosts-file PATH] [--project all|imageclip|modelview] [--dry-run] [--manifest-only] [--delete]"
}

host=""
user="${USER}"
project="all"
dry=()
port="22"
ssh_options=()
manifest_only=false
delete=false
while (($#)); do
  case "$1" in
    --host) host="$2"; shift 2 ;;
    --user) user="$2"; shift 2 ;;
    --port) port="$2"; shift 2 ;;
    --known-hosts-file) ssh_options+=(-o "UserKnownHostsFile=$2" -o StrictHostKeyChecking=yes); shift 2 ;;
    --project) project="$2"; shift 2 ;;
    --dry-run) dry=(--dry-run); shift ;;
    --manifest-only) manifest_only=true; shift ;;
    --delete) delete=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done
[[ -n "${host}" ]] || { usage >&2; exit 2; }
[[ "${port}" =~ ^[0-9]+$ ]] && ((port >= 1 && port <= 65535)) || { usage >&2; exit 2; }
[[ "${project}" =~ ^(all|imageclip|modelview)$ ]] || { usage >&2; exit 2; }

imageclip_root="${IMAGECLIP_ROOT:-/opt/imageclip}"
modelview_root="${MODELVIEW_ROOT:-/opt/modelviewcreator}"
options=(-a --partial --append-verify --human-readable --progress)
if [[ "${manifest_only}" == true ]]; then
  options+=(--include='models.manifest.yaml' --exclude='*')
fi
if [[ "${delete}" == true ]]; then
  read -r -p "确认删除远端对应项目中、本机不存在的模型？输入 DELETE: " answer
  [[ "${answer}" == DELETE ]] || exit 1
  options+=(--delete)
fi

sync_tree() {
  local label="$1" source_dir="$2" target_dir="$3"
  [[ -d "${source_dir}" ]] || { echo "错误：${label} 模型目录不存在：${source_dir}" >&2; exit 1; }
  echo "同步 ${label}: ${source_dir}/ -> ${user}@${host}:${target_dir}/"
  ssh -p "${port}" "${ssh_options[@]}" "${user}@${host}" "mkdir -p '${target_dir}'"
  rsync "${options[@]}" "${dry[@]}" -e "ssh -p ${port} ${ssh_options[*]}" "${source_dir}/" "${user}@${host}:${target_dir}/"
}

if [[ "${project}" == "all" || "${project}" == "imageclip" ]]; then
  sync_tree "ImageClip" "${imageclip_root}/models" "/opt/imageclip/models"
  if ((${#dry[@]} == 0)); then
    # ComfyUI-nunchaku registers these optional model categories during import.
    # The shared model root is mounted read-only in ComfyUI, so the directories
    # must exist on the host before the container starts.
    ssh -p "${port}" "${ssh_options[@]}" "${user}@${host}" \
      "mkdir -p /opt/imageclip/models/pulid /opt/imageclip/models/insightface /opt/imageclip/models/facexlib /opt/imageclip/models/ipadapter /opt/imageclip/models/clip"
  fi
fi
if [[ "${project}" == "all" || "${project}" == "modelview" ]]; then
  if [[ "${manifest_only}" == true ]]; then
    echo "ModelViewCreator manifest 由 GPU Control 仓库管理；跳过模型文件。"
  else
    sync_tree "ModelViewCreator" "${modelview_root}/model" "/opt/modelviewcreator/model"
    if ((${#dry[@]} == 0)); then
      ssh -p "${port}" "${ssh_options[@]}" "${user}@${host}" '
        set -eu
        mkdir -p /opt/imageclip/models
        if [ -e /opt/imageclip/models/SEEDVR2 ] && [ ! -L /opt/imageclip/models/SEEDVR2 ]; then
          echo "错误：远程 /opt/imageclip/models/SEEDVR2 存在且不是软链接" >&2
          exit 1
        fi
        ln -sfn /opt/modelviewcreator/model/SEEDVR2 /opt/imageclip/models/SEEDVR2

        # The approved roughness workflow stores its loaders in category
        # subdirectories. Keep one canonical model copy and expose only links.
        mkdir -p \
          /opt/modelviewcreator/model/lora/qwen \
          /opt/modelviewcreator/model/lora/qwen-2511 \
          /opt/modelviewcreator/model/unet/gguf
        if [ -f /opt/modelviewcreator/model/lora/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors ]; then
          ln -sfn ../Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors \
            /opt/modelviewcreator/model/lora/qwen/Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors
        fi
        if [ -f /opt/modelviewcreator/model/lora/roughness_qwen2511_v1_000006000.safetensors ]; then
          ln -sfn ../roughness_qwen2511_v1_000006000.safetensors \
            /opt/modelviewcreator/model/lora/qwen-2511/roughness_qwen2511_v1_000006000.safetensors
        fi
        if [ -f /opt/modelviewcreator/model/unet/qwen-image-edit-2511-Q4_K_M.gguf ]; then
          ln -sfn ../qwen-image-edit-2511-Q4_K_M.gguf \
            /opt/modelviewcreator/model/unet/gguf/qwen-image-edit-2511-Q4_K_M.gguf
        fi
      '
    fi
  fi
fi

if ((${#dry[@]})); then
  echo "dry-run 完成：没有写入远端，也不执行远端 SHA-256 校验。"
elif [[ "${manifest_only}" == true ]]; then
  echo "manifest-only 完成：模型文件未同步。"
else
  ssh -p "${port}" "${ssh_options[@]}" "${user}@${host}" 'cd /opt/gpu-control && scripts/verify_comfy_projects.sh'
fi
