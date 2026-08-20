#!/usr/bin/env bash
set -euo pipefail

BLENDER_VERSION="${BLENDER_VERSION:-5.1.2}"
BLENDER_SERIES="${BLENDER_SERIES:-5.1}"
BLENDER_ARCH="${BLENDER_ARCH:-linux-x64}"
BLENDER_ARCHIVE="blender-${BLENDER_VERSION}-${BLENDER_ARCH}.tar.xz"
BLENDER_URL="${BLENDER_URL:-https://download.blender.org/release/Blender${BLENDER_SERIES}/${BLENDER_ARCHIVE}}"
BLENDER_INSTALL_ROOT="${BLENDER_INSTALL_ROOT:-$HOME/.local/opt}"
BLENDER_TARGET="${BLENDER_INSTALL_ROOT}/blender-${BLENDER_VERSION}"
BLENDER_LINK="${HOME}/.local/bin/blender"

case "${BLENDER_VERSION}:${BLENDER_ARCH}" in
  "5.1.2:linux-x64")
    EXPECTED_SHA256="${BLENDER_SHA256:-aaccb355f50183979b698bcce7467103a76261b5fa59f4972295842662a285fb}"
    ;;
  *)
    if [[ -z "${BLENDER_SHA256:-}" ]]; then
      echo "BLENDER_SHA256 is required for an unpinned Blender build." >&2
      exit 2
    fi
    EXPECTED_SHA256="$BLENDER_SHA256"
    ;;
esac

install_system_packages() {
  command -v apt-get >/dev/null 2>&1 || return 0

  local runner=()
  if [[ "$(id -u)" -eq 0 ]]; then
    runner=()
  elif command -v sudo >/dev/null 2>&1; then
    runner=(sudo)
  else
    echo "Skipping apt packages: neither root nor sudo is available." >&2
    return 0
  fi

  "${runner[@]}" apt-get update
  "${runner[@]}" apt-get install -y --no-install-recommends \
    ca-certificates curl xz-utils \
    libdbus-1-3 libegl1 libfontconfig1 libfreetype6 libgl1 libglib2.0-0 \
    libice6 libsm6 libx11-6 libxfixes3 libxi6 libxkbcommon0 \
    libxrender1 libxxf86vm1
}

install_blender() {
  if [[ -x "${BLENDER_TARGET}/blender" ]]; then
    return 0
  fi

  install_system_packages
  command -v curl >/dev/null 2>&1 || {
    echo "curl is required to download Blender." >&2
    exit 3
  }
  command -v sha256sum >/dev/null 2>&1 || {
    echo "sha256sum is required to verify Blender." >&2
    exit 3
  }

  local temp_dir archive_path extracted_dir
  temp_dir="$(mktemp -d)"
  trap 'rm -rf "$temp_dir"' RETURN
  archive_path="${temp_dir}/${BLENDER_ARCHIVE}"

  curl --fail --location --retry 3 --retry-delay 2 \
    --output "$archive_path" "$BLENDER_URL"
  printf '%s  %s\n' "$EXPECTED_SHA256" "$archive_path" | sha256sum --check -

  tar -xJf "$archive_path" -C "$temp_dir"
  extracted_dir="${temp_dir}/blender-${BLENDER_VERSION}-${BLENDER_ARCH}"
  if [[ ! -x "${extracted_dir}/blender" ]]; then
    echo "Downloaded Blender archive has an unexpected layout." >&2
    exit 4
  fi

  mkdir -p "$BLENDER_INSTALL_ROOT"
  rm -rf "$BLENDER_TARGET"
  mv "$extracted_dir" "$BLENDER_TARGET"
}

persist_environment() {
  mkdir -p "$(dirname "$BLENDER_LINK")"
  ln -sfn "${BLENDER_TARGET}/blender" "$BLENDER_LINK"

  touch "$HOME/.bashrc"
  grep -Fqx 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.bashrc" ||
    printf '%s\n' 'export PATH="$HOME/.local/bin:$PATH"' >>"$HOME/.bashrc"
  grep -Fqx 'export BLENDER_BIN="$HOME/.local/bin/blender"' "$HOME/.bashrc" ||
    printf '%s\n' 'export BLENDER_BIN="$HOME/.local/bin/blender"' >>"$HOME/.bashrc"
}

install_blender
persist_environment
"$BLENDER_LINK" --version
echo "BLENDER_BIN=$BLENDER_LINK"
