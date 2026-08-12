import hashlib
import subprocess
import sys
from pathlib import Path

from packages.gpu_control_core.assets import (
    RETOPOLOGY_DIRECT_V2_PACKAGE_SHA256,
    RETOPOLOGY_DIRECT_V2_PACKAGE_VERSION,
    retopology_direct_v2_completion_identity_valid,
)

ROOT = Path("resources/retopology-direct-v2")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def test_approved_v319_package_is_complete() -> None:
    completed = subprocess.run(  # noqa: S603 - repository-owned verifier
        [sys.executable, str(ROOT / "server" / "verify_package.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (ROOT / "server" / "batch_retopology.py").is_file()
    assert (
        file_sha256(ROOT / "blender-auto-retopo-align" / "SKILL.md")
        == "d2bcdb1dede2e5bddeb10a913cede28e3496e4bbfeb413b0bf6355248ec4376b"
    )
    assert RETOPOLOGY_DIRECT_V2_PACKAGE_VERSION == "3.0.19"
    assert (
        RETOPOLOGY_DIRECT_V2_PACKAGE_SHA256
        == "72d2216590c79b436f9e0125e3c2074ec6d576db9f80a15e49d9a88561c550dd"
    )


def test_gpu_control_uses_scheduler_fanout_not_upstream_serial_batch() -> None:
    worker = Path("apps/blender_worker/src/gpu_control_blender_worker/main.py").read_text(
        encoding="utf-8"
    )
    assert "one_click_retopology.py" in worker
    assert "batch_retopology.py" not in worker
    assert '"CODEX_BIN": "/app/packages/asset_processing/codex_job_launcher.py"' in worker


def test_direct_v2_task_auth_uses_the_rotated_node_private_credential() -> None:
    worker = Path("apps/blender_worker/src/gpu_control_blender_worker/main.py").read_text(
        encoding="utf-8"
    )
    assert 'persistent_auth_source = Path(environment["CODEX_HOME"]) / "auth.json"' in worker
    assert '"CODEX_AUTH_SOURCE": str(persistent_auth_source)' in worker
    assert '"CODEX_AUTH_WRITEBACK_DESTINATION": str(persistent_auth_source)' in worker


def test_rolling_completion_accepts_only_matching_approved_package_identity() -> None:
    current = {
        "package_version": RETOPOLOGY_DIRECT_V2_PACKAGE_VERSION,
        "package_sha256": RETOPOLOGY_DIRECT_V2_PACKAGE_SHA256,
    }
    previous = {
        "package_version": "3.0.14",
        "package_sha256": ("4cd05689d1171e8d75a4546ccc737d3ba82fb31d90971758671802fe5ef0c5e9"),
    }
    assert retopology_direct_v2_completion_identity_valid(current, current) is True
    assert retopology_direct_v2_completion_identity_valid(previous, previous) is True
    assert retopology_direct_v2_completion_identity_valid(current, previous) is False
    unknown = {**current, "package_version": "3.0.1"}
    assert retopology_direct_v2_completion_identity_valid(unknown, unknown) is False


def test_public_create_contract_selects_v319_without_changing_route() -> None:
    api = Path("apps/asset_api/src/gpu_control_asset_api/main.py").read_text(encoding="utf-8")
    assert '@app.post("/api/v1/assets/retopology/process")' in api
    assert '"schema_version": "retopology_input.direct-v2"' in api
    assert '"engine_contract": "retopology-direct-v2"' in api
    assert "RETOPOLOGY_DIRECT_V2_PACKAGE_VERSION" in api
    assert '"schema_version": "retopology_direct_delivery.v8"' in Path(
        "apps/blender_worker/src/gpu_control_blender_worker/main.py"
    ).read_text("utf-8")


def test_v319_region_method_routing_and_fast_delivery_contract_are_wired() -> None:
    entrypoint = (ROOT / "server" / "one_click_retopology.py").read_text("utf-8")
    worker = Path("apps/blender_worker/src/gpu_control_blender_worker/main.py").read_text(
        "utf-8"
    )
    assert "shape_validation_command" not in entrypoint
    assert "alignment_views_dir" not in entrypoint
    assert '"topology_gate": "no_broken_faces"' in entrypoint
    assert '"uv_policy": "preserve_optional"' in entrypoint
    assert '"shape_validation": sidecar' not in worker
    assert '"alignment_views": sidecar' not in worker
    prompt = (ROOT / "server/agent_prompt.md").read_text("utf-8")
    assert "不生成 UV" in prompt
    assert "不执行 FBX 重新导入验证" in prompt
    assert "开放边、非流形边、游离点边、重复点面和面朝向仅记录为诊断" in prompt
    assert "ATTEMPT_GUIDANCE" in prompt
    assert "semantic_measurements" in prompt
    assert "$blender-retopology-compare-iterate" in prompt
    assert "$blender-auto-retopo-align" in prompt
    assert "禁止根据对象名、文件名、全局 AABB、网格岛数量或单一面数阈值猜测模型身份" in prompt
    assert "用户已明确取消交付前的方向审查" in prompt
    assert "不得运行训练技能的 pair audit、topology-flow audit" in prompt
    assert "TOPOLOGY_SKILL_ID = \"blender-retopology-compare-iterate\"" in entrypoint
    assert '"RETOPOLOGY_SKILL_ROOT": str(installed_topology_skill)' in entrypoint
    assert '"RETOPOLOGY_ALIGNMENT_SKILL_ROOT": str(installed_skill)' in entrypoint
    assert "不得运行计划守卫" in prompt
    assert "最终有效 Blend 和无破面结果仍是唯一交付门禁" in prompt
    assert "controlled_direct_reduction" in prompt
    assert "复杂连续模型或复杂软表面" in prompt
    assert "布料、皮革" in prompt
    assert "region_method_map" in prompt
    assert "木堆、石堆、碎料堆" in prompt
    assert "disconnected mesh island 数量绝不等于语义组件数量" in prompt
    assert "禁止用面数阈值" in prompt
    assert "一次且仅一次有界高模只读分析" in prompt
    assert "512×512" in prompt
    assert "guard_shape_authority_plan.py" not in prompt
    assert '"timing_seconds"' in entrypoint
    prepare = (
        ROOT / "blender-auto-retopo-align/scripts/prepare_fbx_source.py"
    ).read_text("utf-8")
    assert "semantic_component_measurements" in prepare
    assert '"render_measurements_required": False' in prepare
    assert '"--attempt-number"' in worker
