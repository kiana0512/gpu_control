#!/usr/bin/env bash
set -Eeuo pipefail

INPUT_IMAGE="${1:-}"
OUTPUT_IMAGE="${2:-/tmp/modelview-inpaint-result.png}"
API_URL="${GPU_CONTROL_URL:-https://10.3.34.11}/api/v1/services/modelview-inpaint"
HEADERS_FILE="${OUTPUT_IMAGE}.headers"

if [[ -z "${INPUT_IMAGE}" || ! -f "${INPUT_IMAGE}" ]]; then
  echo "usage: $0 /path/to/input.png [/path/to/result.png]" >&2
  exit 2
fi

restore_reserved() {
  docker exec gpu-control-postgres-1 sh -lc \
    'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "update nodes set mode='"'"'RESERVED'"'"', manual_reserved=true where id='"'"'control-4090'"'"';"' \
    >/dev/null 2>&1 || true
}
trap restore_reserved EXIT INT TERM

echo "[1/4] unloading stale ComfyUI models"
docker exec gpu-control-api-1 python -c \
  'import httpx; r=httpx.post("http://comfyui-4090:8188/free", json={"unload_models": True, "free_memory": True}, timeout=30); r.raise_for_status()'

echo "[2/4] temporarily enabling the 4090 scheduler slot"
docker exec gpu-control-postgres-1 sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -c "update nodes set mode='"'"'ACTIVE'"'"', manual_reserved=false where id='"'"'control-4090'"'"';"' \
  >/dev/null

echo "[3/4] submitting a real image and waiting for the final image"
HTTP_CODE="$(curl -ksS \
  -D "${HEADERS_FILE}" \
  -o "${OUTPUT_IMAGE}" \
  -w '%{http_code}' \
  -H "Idempotency-Key: modelview-e2e-$(date +%s)" \
  -F "image=@${INPUT_IMAGE}" \
  "${API_URL}")"

if [[ "${HTTP_CODE}" != "200" ]]; then
  echo "request failed: HTTP ${HTTP_CODE}" >&2
  cat "${OUTPUT_IMAGE}" >&2
  exit 1
fi

echo "[4/4] validating the returned artifact"
file "${OUTPUT_IMAGE}"
sha256sum "${OUTPUT_IMAGE}"
grep -iE '^(x-job-id|x-client-id|content-type):' "${HEADERS_FILE}" || true
echo "result: ${OUTPUT_IMAGE}"
