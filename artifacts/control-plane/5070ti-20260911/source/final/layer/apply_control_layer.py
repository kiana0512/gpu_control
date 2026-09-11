"""Install only verified compatibility modules over the reviewed HMAC images."""

import base64
import csv
import hashlib
import importlib.metadata
import json
import os
import shutil
from pathlib import Path

dist = importlib.metadata.distribution("gpu-control")
assert dist.version == "1.5.23.post2"
site = Path(dist.locate_file(""))
component = os.environ["GPU_CONTROL_RESOURCE_COMPONENT"]
manifest = json.loads(Path("/tmp/control-layer/base-modules.json").read_text())
changed = []
for relative, detail in manifest.items():
    if detail["component"] not in ("common", component):
        continue
    target = site / relative
    assert hashlib.sha256(target.read_bytes()).hexdigest() == detail["base_sha256"], relative
    content = (Path("/tmp/control-layer") / detail["source"]).read_bytes()
    assert hashlib.sha256(content).hexdigest() == detail["new_sha256"], relative
    target.write_bytes(content)
    changed.append(relative)

before = site / "gpu_control-1.5.23.post2.dist-info"
after = site / "gpu_control-1.5.23.post3.dist-info"
assert before.is_dir() and not after.exists()
records = list(csv.reader((before / "RECORD").open(newline="")))
shutil.copytree(before, after)
shutil.rmtree(before)
metadata = (after / "METADATA").read_text()
assert metadata.count("\nVersion: 1.5.23.post2\n") == 1
(after / "METADATA").write_text(metadata.replace("\nVersion: 1.5.23.post2\n", "\nVersion: 1.5.23.post3\n"))
changed.append(f"{after.name}/METADATA")
evidence = after / "gpu-control-final-control-layer.json"
evidence.write_text(json.dumps({
    "source_revision": os.environ["GPU_CONTROL_BUILD_REVISION"],
    "component": component,
    "scope": "terminal batch reconciliation guard and authoritative Codex freshness limits",
    "modules": manifest,
}, indent=2) + "\n")
records.append([f"{after.name}/{evidence.name}", "", ""])
changed.append(f"{after.name}/{evidence.name}")
for row in records:
    row[0] = row[0].replace(before.name + "/", after.name + "/", 1)
    if row[0] in changed:
        content = (site / row[0]).read_bytes()
        row[1] = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(content).digest()).decode().rstrip("=")
        row[2] = str(len(content))
with (after / "RECORD").open("w", newline="") as stream:
    csv.writer(stream).writerows(records)
print("VERIFIED_FINAL_CONTROL_LAYER", component, len(changed))
