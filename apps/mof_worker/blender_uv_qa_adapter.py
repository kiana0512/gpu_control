"""Run the pinned UV QA script with GPU Control's aligned UV-area tolerance."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

SKILL_QA_SHA256 = "bbabf207a60703ec0d63ce4aa78f66ff69cb338e7e0696eac95be856c8700d5d"
ALIGNED_EPSILON = 1.0e-12


def main() -> None:
    skill_qa = Path(__file__).with_name("qa_uv.py")
    digest = hashlib.sha256(skill_qa.read_bytes()).hexdigest()
    if digest != SKILL_QA_SHA256:
        raise RuntimeError("pinned blender-pbr-uv QA script SHA-256 mismatch")
    specification = importlib.util.spec_from_file_location("pinned_blender_pbr_uv_qa", skill_qa)
    if specification is None or specification.loader is None:
        raise RuntimeError("unable to load pinned blender-pbr-uv QA script")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    module.__dict__["EPS"] = ALIGNED_EPSILON
    print(f"GPU_CONTROL_MOF_UV_QA_POLICY skill_sha256={digest} epsilon={ALIGNED_EPSILON:.0e}")
    module.main()


if __name__ == "__main__":
    main()
