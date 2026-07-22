#!/usr/bin/env bash
set -Eeuo pipefail
usage() { echo "用法: $0 [--manifest FILE] [--root DIR]"; }
root="${MODEL_ROOT:-/srv/comfyui/models}"; manifest=""
while (($#)); do case "$1" in --manifest) manifest="$2"; shift 2;; --root) root="$2"; shift 2;; -h|--help) usage; exit 0;; *) usage >&2; exit 2;; esac; done
manifest="${manifest:-${root}/models.manifest.yaml}"
python3 - "${manifest}" "${root}" <<'PY'
import hashlib, pathlib, sys, yaml
manifest = yaml.safe_load(pathlib.Path(sys.argv[1]).read_text())
root = pathlib.Path(sys.argv[2]).resolve(); failed = 0
models = manifest.get("models", [])
if manifest.get("manifest_version") == "USER_INPUT_REQUIRED" or not models:
    print("MISSING real model manifest entries")
    raise SystemExit(1)
for item in models:
    path = (root / item["path"]).resolve()
    if root not in path.parents or not path.is_file(): print(f"MISSING {item['path']}"); failed += 1; continue
    size = path.stat().st_size
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    if size != int(item["size_bytes"]) or digest != item["sha256"]: print(f"MISMATCH {item['path']}"); failed += 1
    else: print(f"OK {item['path']}")
raise SystemExit(1 if failed else 0)
PY
