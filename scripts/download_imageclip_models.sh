#!/usr/bin/env bash
set -Eeuo pipefail

imageclip_root="${IMAGECLIP_ROOT:-/opt/imageclip}"
model_root="${MODEL_ROOT:-${imageclip_root}/models}"

download_model() {
  local relative_path="$1"
  local expected_size="$2"
  local expected_sha256="$3"
  local url="$4"
  local target="${model_root}/${relative_path}"
  local partial="${target}.part"

  install -d "$(dirname "${target}")"
  if [[ -f "${target}" ]]; then
    echo "校验已有模型: ${relative_path}"
  else
    echo "下载模型: ${relative_path}"
    curl --fail --location --show-error --retry 8 --retry-delay 3 \
      --continue-at - --output "${partial}" "${url}"
    [[ "$(stat -c %s "${partial}")" == "${expected_size}" ]] || {
      echo "文件大小不符: ${partial}" >&2
      return 1
    }
    mv "${partial}" "${target}"
  fi
  printf '%s  %s\n' "${expected_sha256}" "${target}" | sha256sum --check
}

[[ -f "${imageclip_root}/ImageClip.json" ]] || {
  echo "缺少 ImageClip Git 仓库: ${imageclip_root}" >&2
  exit 1
}

download_model \
  unet/flux-2-klein-9b-Q6_K.gguf \
  7865424160 \
  1cd667293607431e79c9e7e01ecf5c602bd00539c2c0f49d4817a62998b5fe98 \
  https://huggingface.co/unsloth/FLUX.2-klein-9B-GGUF/resolve/main/flux-2-klein-9b-Q6_K.gguf

download_model \
  text_encoders/qwen_3_8b_fp8mixed.safetensors \
  8664848742 \
  abad16806e0cbabc54e0325d6565847443fe396d5f0be38bb3cd3fe75a1201d6 \
  https://huggingface.co/Comfy-Org/flux2-klein-9B/resolve/main/split_files/text_encoders/qwen_3_8b_fp8mixed.safetensors

download_model \
  vae/flux2-vae.safetensors \
  336213556 \
  d64f3a68e1cc4f9f4e29b6e0da38a0204fe9a49f2d4053f0ec1fa1ca02f9c4b5 \
  https://huggingface.co/Comfy-Org/flux2-dev/resolve/main/split_files/vae/flux2-vae.safetensors

[[ -f "${model_root}/loras/Koutu_Flux2klein_v2_000007250.safetensors" ]] || {
  echo "缺少 ImageClip 仓库自带 LoRA" >&2
  exit 1
}

echo "ImageClip 模型已下载并校验完成。"
