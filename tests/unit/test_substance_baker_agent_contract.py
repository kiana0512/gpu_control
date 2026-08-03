from pathlib import Path


def test_substance_baker_fence_preserves_process_without_model_eviction() -> None:
    source = (
        Path(__file__).parents[2]
        / "apps"
        / "substance_baker_agent"
        / "Invoke-GPUControlSubstanceAgent.ps1"
    ).read_text(encoding="utf-8")

    assert "function Assert-ComfyUiProcessStable" in source
    assert "Set-ComfyUiRunning" not in source
    assert "docker stop" not in source
    assert "docker start" not in source
    assert "/free" not in source
    assert "{{.State.StartedAt}}" in source
    assert "{{.RestartCount}}" in source
    assert "ComfyUI process changed during native Baker fence" in source
    assert "Read-BakerFenceJobs" not in source
    assert "Write-BakerFenceJobs" not in source
    assert "comfyui_cache_policy = 'no_explicit_eviction_process_preserved'" in source
    assert "comfyui_container_restarted = $false" in source
    assert "SUBSTANCE_COMFYUI_CONTINUITY_FAILED" in source
