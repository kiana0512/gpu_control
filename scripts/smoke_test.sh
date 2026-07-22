#!/usr/bin/env bash
set -Eeuo pipefail
[[ "${1:-}" == -h || "${1:-}" == --help ]] && { echo "用法: $0 [BASE_URL] [--ca CERT|--insecure]"; exit 0; }
base="${1:-https://127.0.0.1}"
shift || true
curl_args=(-fsS)
while (($#)); do
  case "$1" in
    --ca) curl_args+=(--cacert "$2"); shift 2 ;;
    --insecure) curl_args+=(-k); shift ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done
curl "${curl_args[@]}" "${base}/health/live" | jq -e '.status=="live"'
curl "${curl_args[@]}" "${base}/health/ready" | jq -e '.status=="ready"'
echo "API 存活与就绪检查通过。"
