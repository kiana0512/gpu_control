#!/usr/bin/env bash
set -Eeuo pipefail

WHITE_MODEL_IMAGE="${1:-}"
MATERIAL_IMAGE="${2:-}"
VIEWPORT_REFERENCE_IMAGE="${3:-}"
OUTPUT_IMAGE="${4:-/tmp/modelview-inpaint-result.png}"
API_URL="${GPU_CONTROL_URL:-https://10.3.34.11}/api/v1/services/modelview-inpaint"
CA_FILE="${GPU_CONTROL_CA:-deploy/control-plane/nginx/certs/lan-ca.crt}"
HEADERS_FILE="${OUTPUT_IMAGE}.headers"

for required in "${WHITE_MODEL_IMAGE}" "${MATERIAL_IMAGE}" "${VIEWPORT_REFERENCE_IMAGE}"; do
  if [[ -z "${required}" || ! -f "${required}" ]]; then
    echo "usage: $0 WHITE_MODEL SIX_VIEW VIEWPORT_REFERENCE [/path/to/result.png]" >&2
    exit 2
  fi
done

[[ -f "${CA_FILE}" ]] || { echo "missing CA file: ${CA_FILE}" >&2; exit 2; }

echo "[1/2] submitting the three public inputs and waiting for the final image"
HTTP_CODE="$(curl --fail-with-body --silent --show-error \
  --cacert "${CA_FILE}" \
  -D "${HEADERS_FILE}" \
  -o "${OUTPUT_IMAGE}" \
  -w '%{http_code}' \
  -H "Idempotency-Key: modelview-e2e-$(date +%s)" \
  -F "image=@${WHITE_MODEL_IMAGE}" \
  -F "material_image=@${MATERIAL_IMAGE}" \
  -F "viewport_reference=@${VIEWPORT_REFERENCE_IMAGE}" \
  "${API_URL}")"

if [[ "${HTTP_CODE}" != "200" ]]; then
  echo "request failed: HTTP ${HTTP_CODE}" >&2
  cat "${OUTPUT_IMAGE}" >&2
  exit 1
fi

echo "[2/2] validating the returned artifact"
file "${OUTPUT_IMAGE}"
sha256sum "${OUTPUT_IMAGE}"
grep -iE '^(x-job-id|x-client-id|content-type):' "${HEADERS_FILE}" || true
echo "result: ${OUTPUT_IMAGE}"
