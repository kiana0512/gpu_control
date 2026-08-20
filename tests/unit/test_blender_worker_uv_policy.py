from pathlib import Path

import pytest
from gpu_control_blender_worker.main import uv_qa_blender_arguments


@pytest.mark.parametrize(
    ("job_type", "strict_expected"),
    [("UV_PROCESS_V2", False), ("UV_UNWRAP", True)],
)
def test_uv_qa_strictness_is_owned_by_asset_api_for_v2(
    tmp_path: Path, job_type: str, strict_expected: bool
) -> None:
    arguments = uv_qa_blender_arguments(
        tmp_path / "qa-adapter.py",
        tmp_path / "model.blend",
        tmp_path / "qa.json",
        job_type,
    )

    assert "--require-max-compatible-shells" in arguments
    assert ("--strict" in arguments) is strict_expected
