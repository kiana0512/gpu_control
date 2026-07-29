#!/usr/bin/env bash
set -euo pipefail

# RetopoFlow is externally owned. Keep its exact official checkout in host-local
# runtime storage; do not vendor it into GPU Control or redistribute it in images.
repo_url="https://github.com/CGCookie/retopoflow.git"
revision="${RETOPOFLOW_REVISION:-ac2570c5292c1dd90190fd3641b4dbc42cf4bd63}"
runtime_root="${RETOPOFLOW_RUNTIME_ROOT:-/opt/gpu-control/runtime/retopoflow}"
checkout="$runtime_root/RetopoFlow"

install -d -m 0755 "$runtime_root"
if [[ ! -d "$checkout/.git" ]]; then
  git clone --filter=blob:none "$repo_url" "$checkout"
fi
git -C "$checkout" fetch --depth=1 origin "$revision"
git -C "$checkout" checkout --detach FETCH_HEAD
actual="$(git -C "$checkout" rev-parse HEAD)"
[[ "$actual" == "$revision" ]] || {
  echo "RetopoFlow revision mismatch: expected=$revision actual=$actual" >&2
  exit 1
}
printf 'RETOPOFLOW_REVISION=%s\n' "$actual"
python3 - "$checkout/__init__.py" <<'PY'
import ast
import pathlib
import sys

module = ast.parse(pathlib.Path(sys.argv[1]).read_text("utf-8"))
for node in module.body:
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "bl_info":
                value = ast.literal_eval(node.value)
                print("RETOPOFLOW_VERSION=" + ".".join(map(str, value["version"])))
                raise SystemExit(0)
raise SystemExit("RetopoFlow bl_info not found")
PY
