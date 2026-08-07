from pathlib import Path


def test_bake_alignment_script_is_immutable_copy_based_and_in_worker_image() -> None:
    root = Path(__file__).parents[2]
    script = (
        root / "resources" / "bake-coordinate-alignment" / "prepare_bake_alignment.py"
    ).read_text(encoding="utf-8")
    dockerfile = (root / "apps" / "blender_worker" / "Dockerfile").read_text(
        encoding="utf-8"
    )

    assert "input_world_to_high_world" in script
    assert "bounds are not used to infer transforms" in script
    assert '"bake_high.fbx"' in script
    assert '"bake_low.fbx"' in script
    assert '"bake_cage.fbx"' in script
    assert "obj.data = obj.data.copy()" in script
    assert "pca" not in script.lower()
    assert "COPY resources/bake-coordinate-alignment /opt/li3d/bake-coordinate-alignment" in dockerfile


def test_high_low_bakes_are_gated_before_substance_and_keep_one_job_id() -> None:
    root = Path(__file__).parents[2]
    worker = (
        root
        / "apps"
        / "blender_worker"
        / "src"
        / "gpu_control_blender_worker"
        / "main.py"
    ).read_text(encoding="utf-8")
    api = (
        root
        / "apps"
        / "asset_api"
        / "src"
        / "gpu_control_asset_api"
        / "main.py"
    ).read_text(encoding="utf-8")

    assert 'request.get("job_type") != "BAKE_ALIGNMENT_V1"' in worker
    assert '"PREPARING_BAKE_COORDINATES"' in worker
    assert "/bake-alignment-complete" in worker
    assert '@app.post("/internal/v1/assets/jobs/{job_id}/bake-alignment-complete")' in api
    assert 'job.job_type = "SUBSTANCE_BAKE_V1"' in api
    assert 'job.source_filename = "substance_bake_input.zip"' in api
    assert 'kind="alignment_report"' in api
    assert 'detail={"code": "BAKE_ALIGNMENT_FAILED"}' in api
