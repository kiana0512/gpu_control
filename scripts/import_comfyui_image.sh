#!/usr/bin/env bash
set -Eeuo pipefail
usage() { echo "用法: $0 --input FILE.tar.gz"; }
input=""
while (($#)); do case "$1" in --input) input="$2"; shift 2;; -h|--help) usage; exit 0;; *) usage >&2; exit 2;; esac; done
[[ -n "${input}" && -f "${input}" && -f "${input}.sha256" ]] || { usage >&2; exit 2; }
(cd "$(dirname "${input}")" && sha256sum --check "$(basename "${input}").sha256")
gzip -dc "${input}" | docker load
