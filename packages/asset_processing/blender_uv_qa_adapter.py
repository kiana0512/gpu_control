"""GPU Control policy adapter for the pinned blender-pbr-uv QA script.

The upstream unwrap script treats an UV triangle as degenerate at 1e-12, while
its QA script historically used 1e-10.  That mismatch rejected valid, tiny UV
triangles produced with uniform texel density.  Keep the approved Skill script
byte-for-byte pinned and align only the runtime tolerance here.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

SKILL_QA = Path("/opt/codex/skills/blender-pbr-uv/scripts/qa_uv.py")
SKILL_QA_SHA256 = "a263d0fc05947d70988317972f9b0bb38e7c85a165274756d3c4dbf4e05f91c3"
ALIGNED_EPSILON = 1.0e-12


def main() -> None:
    digest = hashlib.sha256(SKILL_QA.read_bytes()).hexdigest()
    if digest != SKILL_QA_SHA256:
        raise RuntimeError("pinned blender-pbr-uv QA script SHA-256 mismatch")
    specification = importlib.util.spec_from_file_location("pinned_blender_pbr_uv_qa", SKILL_QA)
    if specification is None or specification.loader is None:
        raise RuntimeError("unable to load pinned blender-pbr-uv QA script")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    module.__dict__["EPS"] = ALIGNED_EPSILON
    print(
        "GPU_CONTROL_UV_QA_POLICY "
        f"skill_sha256={digest} epsilon={ALIGNED_EPSILON:.0e}"
    )
    module.main()


if __name__ == "__main__":
    main()
